from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path
from typing import Any
import csv
import json
import os
import re
import subprocess
import sys
import time
from urllib import error, request

from app.models.runs import CodeArtifact, CreatedAsset, ExecutorManifestV2, ManagerReport, TaskPacket
from app.services.utils import atomic_write_json


MAX_INPUT_BYTES = 24_000
PROVIDER_RETRY_DELAYS_SECONDS = [1, 2, 4, 8]


def _emit(payload: dict[str, Any]) -> None:
    print("BP_EVENT " + json.dumps(payload, ensure_ascii=False), flush=True)


def _report_complete(run_dir: Path) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(run_dir / "report_executor_result.py"),
            "complete",
            "--manifest",
            str(run_dir / "manifest.candidate.json"),
        ],
        cwd=run_dir,
        check=False,
    ).returncode


def _report_fail(run_dir: Path, *, summary: str, reason_code: str = "execution_error", details: dict[str, Any] | None = None) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(run_dir / "report_executor_result.py"),
            "fail",
            "--reason-code",
            reason_code,
            "--summary",
            summary,
            "--details-json",
            json.dumps(details or {}, ensure_ascii=False),
        ],
        cwd=run_dir,
        check=False,
    ).returncode


def _read_text_preview(path: Path) -> str:
    if not path.exists():
        return "[missing]"
    data = path.read_bytes()[:MAX_INPUT_BYTES]
    text = data.decode("utf-8", errors="replace")
    if path.stat().st_size > MAX_INPUT_BYTES:
        text += "\n[truncated]"
    return text


def _extract_text(response_payload: dict[str, Any]) -> str:
    blocks = response_payload.get("content") or []
    return "\n".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _extract_json(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("DeepSeek response did not contain a JSON object.")
    return json.loads(text[start : end + 1])


def _is_rna_prep_task(packet: TaskPacket) -> bool:
    text = " ".join([packet.card_id, packet.card_title or "", packet.goal]).lower()
    return (
        "rna_prep" in text
        or ("count" in text and ("filter" in text or "过滤" in text))
        or ("计数矩阵" in text and "过滤" in text)
    )


def _sample_group(sample_name: str) -> str:
    normalized = sample_name.lower()
    if normalized.startswith("oaa"):
        return "OAA"
    if normalized.startswith("2d-gal") or normalized.startswith("2d_gal"):
        return "2D-gal"
    return "unknown"


def _find_count_input(packet: TaskPacket, project_root: Path) -> Path | None:
    for asset in packet.input_assets:
        path = project_root / asset.path
        if not path.exists() or path.is_dir():
            continue
        preview = _read_text_preview(path)
        header = preview.splitlines()[0].split("\t") if preview.splitlines() else []
        if "Geneid" in header and len(header) > 3:
            return path
    return None


def _expected_path(packet: TaskPacket, role: str, fallback_name: str) -> str:
    for output in packet.expected_outputs:
        if output.role == role:
            return output.path_hint
    result_dir = packet.run_context.result_dir if packet.run_context else f"results/{packet.card_id}/{packet.task_id}"
    return f"{result_dir}/{fallback_name}"


def _run_rna_prep(packet: TaskPacket, project_root: Path, run_dir: Path) -> int:
    count_path = _find_count_input(packet, project_root)
    if count_path is None:
        raise ValueError("No tab-delimited count matrix input with a Geneid column was found.")

    _emit({"type": "progress_update", "stage": "rna_prep", "progress": 10, "message": "Reading count matrix."})
    with count_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError("Count matrix is empty or missing a header row.")
        annotation_columns = [column for column in ("Geneid", "gene_name", "gene_type") if column in reader.fieldnames]
        sample_columns = [column for column in reader.fieldnames if column not in annotation_columns]
        if not sample_columns:
            raise ValueError("Count matrix does not contain sample count columns.")
        rows = list(reader)

    filtered_rows = []
    invalid_count_values: list[tuple[str, str, str]] = []
    for row in rows:
        passing_samples = 0
        for sample in sample_columns:
            raw_value = row.get(sample, "0")
            try:
                value = float(raw_value or "0")
            except ValueError:
                gene_id = row.get("Geneid") or row.get("gene_name") or "<unknown>"
                invalid_count_values.append((gene_id, sample, str(raw_value)))
                continue
            if value >= 10:
                passing_samples += 1
        if passing_samples >= 3:
            filtered_rows.append(row)

    if invalid_count_values:
        preview = "; ".join(
            f"{gene}/{sample}={value}" for gene, sample, value in invalid_count_values[:10]
        )
        suffix = "" if len(invalid_count_values) <= 10 else f"; ... {len(invalid_count_values) - 10} more"
        raise ValueError(
            f"Count matrix contains non-numeric count values: {preview}{suffix}"
        )

    filtered_counts_rel = _expected_path(packet, "rna_prep_filtered_counts", "rna_prep_filtered_counts.tsv")
    sample_meta_rel = _expected_path(packet, "rna_prep_sample_meta", "rna_prep_sample_meta.tsv")
    summary_rel = _expected_path(packet, "run_summary", "run_summary.md")
    preview_rel = _expected_path(packet, "run_preview", "run_preview.svg")

    filtered_counts_path = project_root / filtered_counts_rel
    sample_meta_path = project_root / sample_meta_rel
    summary_path = project_root / summary_rel
    preview_path = project_root / preview_rel
    for path in (filtered_counts_path, sample_meta_path, summary_path, preview_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    _emit({"type": "progress_update", "stage": "rna_filter", "progress": 45, "message": "Filtering low-expression genes."})
    output_columns = annotation_columns + sample_columns
    with filtered_counts_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(filtered_rows)

    with sample_meta_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["sample_id", "condition"])
        for sample in sample_columns:
            writer.writerow([sample, _sample_group(sample)])

    group_counts: dict[str, int] = {}
    for sample in sample_columns:
        group = _sample_group(sample)
        group_counts[group] = group_counts.get(group, 0) + 1
    warning_messages = []
    if group_counts.get("OAA") != 3 or group_counts.get("2D-gal") != 3:
        warning_messages.append(f"Expected 3 OAA and 3 2D-gal samples; observed {group_counts}.")

    summary = (
        "# 数据准备与质控\n\n"
        f"- 输入矩阵: `{count_path.relative_to(project_root).as_posix()}`\n"
        f"- 原始基因数: {len(rows)}\n"
        f"- 过滤条件: 至少 3 个样本 count >= 10\n"
        f"- 过滤后基因数: {len(filtered_rows)}\n"
        f"- 样本数: {len(sample_columns)} ({', '.join(f'{key}={value}' for key, value in sorted(group_counts.items()))})\n"
    )
    if warning_messages:
        summary += "\n## Warnings\n" + "\n".join(f"- {message}" for message in warning_messages) + "\n"
    summary_path.write_text(summary, encoding="utf-8")

    preview_path.write_text(
        f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="220" viewBox="0 0 640 220">
  <rect width="640" height="220" fill="#f8faf7"/>
  <text x="32" y="42" font-family="sans-serif" font-size="22" fill="#1f2933">RNA prep QC</text>
  <text x="32" y="82" font-family="sans-serif" font-size="16" fill="#334155">Raw genes: {len(rows)}</text>
  <text x="32" y="112" font-family="sans-serif" font-size="16" fill="#334155">Filtered genes: {len(filtered_rows)}</text>
  <text x="32" y="142" font-family="sans-serif" font-size="16" fill="#334155">Samples: {len(sample_columns)}</text>
  <rect x="32" y="166" width="520" height="18" fill="#d9e6d0"/>
  <rect x="32" y="166" width="{0 if not rows else max(1, int(520 * len(filtered_rows) / len(rows)))}" height="18" fill="#4f7f52"/>
</svg>
""",
        encoding="utf-8",
    )

    created_assets = [
        CreatedAsset(role="rna_prep_filtered_counts", path=filtered_counts_rel, description="Filtered count matrix."),
        CreatedAsset(role="rna_prep_sample_meta", path=sample_meta_rel, description="Sample metadata table."),
        CreatedAsset(role="run_summary", path=summary_rel, description="RNA prep summary."),
        CreatedAsset(role="run_preview", path=preview_rel, description="RNA prep SVG preview."),
    ]
    code_dir = project_root / "scripts" / "generated" / packet.task_id
    code_dir.mkdir(parents=True, exist_ok=True)
    code_path = code_dir / "rna_prep_local_filter.py"
    code_path.write_text(
        "\n".join(
            [
                '"""Preserved local RNA prep filtering note for Blueprint validation."""',
                f"RUN_ID = {packet.task_id!r}",
                f"RAW_GENE_COUNT = {len(rows)}",
                f"FILTERED_GENE_COUNT = {len(filtered_rows)}",
                f"SAMPLE_COUNT = {len(sample_columns)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = ExecutorManifestV2(
        schema_version="executor_manifest.v2",
        summary=f"Filtered {len(rows)} genes to {len(filtered_rows)} genes using count>=10 in at least 3 samples.",
        created_assets=created_assets,
        code_artifacts=[
            CodeArtifact(
                path=str(code_path.relative_to(project_root)),
                language="python",
                purpose="Preserved local RNA prep filtering logic summary.",
            )
        ],
        manager_report=ManagerReport(
            summary=f"Filtered {len(rows)} genes to {len(filtered_rows)} genes using count>=10 in at least 3 samples.",
            warnings=warning_messages,
        ),
    )
    atomic_write_json(run_dir / "manifest.candidate.json", manifest.model_dump(exclude_none=True))
    result = _report_complete(run_dir)
    if result != 0:
        return result
    for asset in created_assets:
        _emit({"type": "progress_update", "stage": asset.role, "message": f"Wrote {asset.role}.", "artifacts": [asset.path]})
    _emit({"type": "final_report", "summary": manifest.summary, "warnings": manifest.manager_report.warnings})
    return 0


def _post_deepseek(prompt: str) -> dict[str, Any]:
    api_key = os.environ.get("BLUEPRINT_DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("BLUEPRINT_DEEPSEEK_API_KEY is not configured for pi executor.")
    base_url = os.environ.get("BLUEPRINT_DEEPSEEK_API_BASE_URL", "https://api.deepseek.com/anthropic").rstrip("/")
    model = os.environ.get("BLUEPRINT_EXECUTOR_MODEL", os.environ.get("BLUEPRINT_MANAGER_MODEL", "deepseek-v4-flash"))
    max_tokens = int(os.environ.get("BLUEPRINT_MANAGER_MAX_TOKENS", "2400"))
    temperature = float(os.environ.get("BLUEPRINT_MANAGER_TEMPERATURE", "0.2"))
    timeout = int(os.environ.get("BLUEPRINT_MANAGER_TIMEOUT_SECONDS", "600"))
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": (
            "You are a Blueprint bioinformatics executor. Use only the provided task packet and input file previews. "
            "Return one strict JSON object. Do not include markdown outside JSON."
        ),
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    http_request = request.Request(
        f"{base_url}/v1/messages",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"DeepSeek executor request failed with HTTP {exc.code}: {detail}")
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise last_error from exc
        except error.URLError as exc:
            last_error = RuntimeError(f"DeepSeek executor request failed: {exc}")
        except TimeoutError as exc:
            last_error = RuntimeError(f"DeepSeek executor request timed out: {exc}")
        if attempt < 5:
            delay = PROVIDER_RETRY_DELAYS_SECONDS[min(attempt - 1, len(PROVIDER_RETRY_DELAYS_SECONDS) - 1)]
            _emit(
                {
                    "type": "progress_update",
                    "stage": "provider_retry",
                    "severity": "warning",
                    "message": f"DeepSeek executor API request failed; retrying attempt {attempt + 1}/5 after {delay}s.",
                }
            )
            time.sleep(delay)
    raise last_error or RuntimeError("DeepSeek executor request failed after retries.")


def _build_prompt(packet: TaskPacket, project_root: Path) -> str:
    inputs = []
    for asset in packet.input_assets:
        path = project_root / asset.path
        inputs.append(
            {
                "asset_id": asset.asset_id,
                "title": asset.title,
                "type": asset.type,
                "path": asset.path,
                "content_preview": _read_text_preview(path),
            }
        )
    return json.dumps(
        {
            "task": {
                "run_id": packet.task_id,
                "project_id": packet.project_id,
                "card_id": packet.card_id,
                "card_title": packet.card_title,
                "goal": packet.goal,
                "worker_instructions": packet.worker_instructions,
                "constraints": packet.constraints,
            },
            "inputs": inputs,
            "expected_outputs": [item.model_dump() for item in packet.expected_outputs],
            "required_response_schema": {
                "summary": "string",
                "key_findings": ["string"],
                "warnings": ["string"],
                "metrics": {"metric_name": "number|string|boolean"},
                "artifacts": [
                    {
                        "role": "must exactly match expected_outputs.role",
                        "path": "must match one of the role's allowed output paths and file formats",
                        "description": "string",
                        "content": "complete UTF-8 file content to write",
                    }
                ],
            },
            "rules": [
                "Return every expected output role exactly once.",
                "Prefer expected_outputs.path_hint as the default output path.",
                "For table outputs, write TSV or CSV text content using an allowed format.",
                "For document outputs, write concise markdown, HTML, or text using an allowed format.",
                "For figure outputs, prefer valid standalone SVG unless the contract explicitly prefers another format.",
                "If the previews are insufficient for a scientific conclusion, state that limitation in warnings and findings.",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _write_outputs(packet: TaskPacket, project_root: Path, response: dict[str, Any]) -> list[CreatedAsset]:
    expected = {item.role: item for item in packet.expected_outputs}
    artifacts = response.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Executor JSON missing artifacts array.")
    seen: set[str] = set()
    created: list[CreatedAsset] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("Each artifact must be an object.")
        role = str(artifact.get("role") or "")
        expected_output = expected.get(role)
        if expected_output is None:
            raise ValueError(f"Unexpected artifact role: {role}")
        if role in seen:
            raise ValueError(f"Duplicate artifact role: {role}")
        seen.add(role)
        path = str(artifact.get("path") or "")
        content = artifact.get("content")
        if path not in expected_output.allowed_path_hints():
            raise ValueError(
                f"Artifact path mismatch for {role}: expected one of {', '.join(expected_output.allowed_path_hints())}, got {path}"
            )
        if not isinstance(content, str):
            raise ValueError(f"Artifact content for {role} must be a string.")
        output_path = project_root / path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        created.append(
            CreatedAsset(
                role=role,
                path=path,
                description=str(artifact.get("description") or expected_output.label or role),
            )
        )
        _emit({"type": "progress_update", "stage": role, "message": f"Wrote {role}.", "artifacts": [path]})
    missing = sorted(set(expected) - seen)
    if missing:
        raise ValueError(f"Executor response missing expected output roles: {', '.join(missing)}")
    return created


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--task-packet", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()

    packet_path = Path(args.task_packet)
    run_dir = Path(args.run_dir)
    project_root = Path(args.project_root)
    packet = TaskPacket.model_validate(json.loads(packet_path.read_text(encoding="utf-8")))

    try:
        if _is_rna_prep_task(packet):
            return _run_rna_prep(packet, project_root, run_dir)

        _emit({"type": "progress_update", "stage": "model_request", "progress": 5, "message": "Pi executor is calling DeepSeek."})
        response_payload = _post_deepseek(_build_prompt(packet, project_root))
        response = _extract_json(_extract_text(response_payload))
        _emit({"type": "progress_update", "stage": "model_response", "progress": 60, "message": "DeepSeek returned executor JSON."})
        created_assets = _write_outputs(packet, project_root, response)
        code_dir = project_root / "scripts" / "generated" / packet.task_id
        code_dir.mkdir(parents=True, exist_ok=True)
        code_path = code_dir / "pi_executor_response.json"
        code_path.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = ExecutorManifestV2(
            schema_version="executor_manifest.v2",
            summary=str(response.get("summary") or "Pi executor completed."),
            created_assets=created_assets,
            code_artifacts=[
                CodeArtifact(
                    path=str(code_path.relative_to(project_root)),
                    language="json",
                    purpose="Preserved structured pi executor response.",
                )
            ],
            manager_report=ManagerReport(
                summary=str(response.get("summary") or "Pi executor completed."),
                warnings=response.get("warnings") if isinstance(response.get("warnings"), list) else [],
            ),
        )
        atomic_write_json(run_dir / "manifest.candidate.json", manifest.model_dump(exclude_none=True))
        result = _report_complete(run_dir)
        if result != 0:
            return result
        _emit(
            {
                "type": "final_report",
                "summary": manifest.summary,
                "warnings": manifest.manager_report.warnings,
            }
        )
        return 0
    except Exception as exc:
        _report_fail(run_dir, summary=str(exc), reason_code="execution_error", details={"provider": "pi"})
        _emit({"type": "issue_report", "stage": "pi_executor", "severity": "high", "needs_manager": True, "message": str(exc)})
        print(f"[pi_agent_executor] {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
