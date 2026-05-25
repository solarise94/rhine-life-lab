from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

from pydantic import ValidationError

from app.models.runs import Manifest
from app.services.artifact_format_service import detect_artifact_class, detect_artifact_format
from app.services.utils import atomic_write_json

MAX_MANIFEST_REPAIR_ATTEMPTS = 3
TRACE_LINE_LIMIT = 20
TRACE_FILE_LIMIT = 200
STDOUT_FORWARD_CHAR_LIMIT = 4_000
SENSITIVE_TOKEN_RE = re.compile(r"(api[_-]?key|token|password|secret|sk-[A-Za-z0-9_-]+)", re.IGNORECASE)
OUTPUT_TIMELINE_NAME = "agent_output_timeline.jsonl"
OUTPUT_KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "package_install",
        re.compile(
            r"install\.packages|BiocManager|R CMD INSTALL|conda install|pip install|npm install|"
            r"installing package|downloaded|trying URL|Content type|compil",
            re.IGNORECASE,
        ),
    ),
    ("tool_call", re.compile(r"tool call|function call|run_command|read_file|write_file|edit_file|apply_patch|shell", re.IGNORECASE)),
    ("code_run", re.compile(r"Rscript|python|bash|source\(|Executing|Running|script|analysis\.R|analysis\.py", re.IGNORECASE)),
    ("manifest", re.compile(r"manifest|created_assets|inputs_used|code_artifacts|schema validation|repair", re.IGNORECASE)),
    ("error", re.compile(r"error|failed|exception|traceback|timeout|no such file|not found", re.IGNORECASE)),
    ("result_summary", re.compile(r"result|output files|metric|complete|summary|significant|genes analyzed|PCA|DESeq2", re.IGNORECASE)),
    ("thinking_like", re.compile(r"\bI need\b|\bI'll\b|\bLet me\b|\bwe need\b|plan|思考|需要|计划|检查", re.IGNORECASE)),
]


def _parse_args() -> object:
    parser = ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--task-packet", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--project-root", required=True)
    return parser.parse_args()


def _load_task_packet(packet_path: Path) -> dict:
    return json.loads(packet_path.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _redact_text(value: str) -> str:
    return SENSITIVE_TOKEN_RE.sub("[REDACTED]", value)


def _redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for token in command:
        lowered = token.lower()
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if any(marker in lowered for marker in ("key", "token", "password", "secret")):
            redacted.append("[REDACTED]")
            redact_next = "=" not in token
            continue
        redacted.append(_redact_text(token))
    return redacted


def _write_trace(run_dir: Path, trace: dict[str, Any]) -> None:
    trace["updated_at"] = _utc_now()
    atomic_write_json(run_dir / "agent_trace.json", trace)


def _classify_output_line(line: str) -> str:
    if line.startswith("BP_EVENT "):
        return "bp_event"
    for kind, pattern in OUTPUT_KIND_PATTERNS:
        if pattern.search(line):
            return kind
    return "plain_output"


def _append_output_timeline_event(
    run_dir: Path,
    *,
    trace_started_monotonic: float,
    phase: str,
    attempt: int,
    event_type: str,
    kind: str,
    text: str = "",
    line_number: int | None = None,
    gap_since_previous_seconds: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    redacted_text = _redact_text(text)
    event: dict[str, Any] = {
        "schema_version": "agent_output_timeline.v1",
        "timestamp": _utc_now(),
        "offset_seconds": round(max(0.0, time.monotonic() - trace_started_monotonic), 3),
        "phase": phase,
        "attempt": attempt,
        "event_type": event_type,
        "kind": kind,
        "line_number": line_number,
        "gap_since_previous_seconds": gap_since_previous_seconds,
        "text": redacted_text,
        "char_count": len(redacted_text),
        "redacted": redacted_text != text,
        "metadata": metadata or {},
    }
    with (run_dir / OUTPUT_TIMELINE_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _record_output_kind(attempt_record: dict[str, Any], kind: str, line: str) -> None:
    counts = attempt_record.setdefault("output_kind_counts", {})
    entry = counts.setdefault(kind, {"lines": 0, "chars": 0})
    entry["lines"] += 1
    entry["chars"] += len(line)


def _stdout_preview(line: str) -> tuple[str, bool]:
    if len(line) <= STDOUT_FORWARD_CHAR_LIMIT:
        return line, False
    head = line[:STDOUT_FORWARD_CHAR_LIMIT]
    return f"{head}\n[wrapper] stdout line truncated: original length {len(line)} characters. Full content should be written to result files, not stdout.", True


def _new_trace(*, provider: str, packet: dict, run_dir: Path, project_root: Path, template: str) -> dict[str, Any]:
    return {
        "schema_version": "agent_trace.v1",
        "run_id": packet.get("task_id"),
        "project_id": packet.get("project_id"),
        "card_id": packet.get("card_id"),
        "card_title": packet.get("card_title"),
        "provider": provider,
        "started_at": _utc_now(),
        "finished_at": None,
        "total_duration_seconds": None,
        "status": "running",
        "current_phase": "initializing",
        "project_root": str(project_root),
        "run_dir": str(run_dir),
        "launch_template_preview": _redact_text(template),
        "provider_attempts": [],
        "manifest_validation": [],
        "file_timeline": [],
        "observations": [],
        "updated_at": _utc_now(),
    }


def _record_observation(trace: dict[str, Any], code: str, message: str, *, severity: str = "info") -> None:
    observations = trace.setdefault("observations", [])
    if any(item.get("code") == code and item.get("message") == message for item in observations):
        return
    observations.append({"severity": severity, "code": code, "message": message, "created_at": _utc_now()})


def _relative_path(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        try:
            return path.relative_to(project_root.resolve()).as_posix()
        except (OSError, ValueError):
            return str(path)


def _file_entry(project_root: Path, path: Path, *, category: str, started_monotonic: float) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    if not path.is_file():
        return None
    return {
        "path": _relative_path(project_root, path),
        "category": category,
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        "mtime_offset_seconds": round(max(0.0, time.monotonic() - started_monotonic), 3),
    }


def _collect_file_timeline(project_root: Path, run_dir: Path, packet: dict, *, started_monotonic: float) -> list[dict[str, Any]]:
    paths: list[tuple[Path, str]] = []
    for filename in (
        "manifest.candidate.json",
        "manifest.json",
        "manifest_repair_prompt.md",
        "manager_brief.json",
        "transcript.md",
        OUTPUT_TIMELINE_NAME,
    ):
        paths.append((run_dir / filename, "run_file"))
    for item in packet.get("expected_outputs", []):
        path_hint = item.get("path_hint")
        if path_hint:
            paths.append((project_root / path_hint, f"expected_output:{item.get('role') or ''}"))
    script_dir = project_root / "scripts" / "generated" / str(packet.get("task_id", ""))
    if script_dir.exists():
        paths.extend((path, "generated_script") for path in sorted(script_dir.rglob("*")) if path.is_file())

    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for path, category in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        entry = _file_entry(project_root, path, category=category, started_monotonic=started_monotonic)
        if entry is not None:
            entries.append(entry)
        if len(entries) >= TRACE_FILE_LIMIT:
            break
    return sorted(entries, key=lambda item: (item["mtime"], item["path"]))


def _render_launch_command(
    template: str,
    *,
    provider: str,
    packet_path: Path,
    run_dir: Path,
    project_root: Path,
    prompt_path: Path | None = None,
) -> list[str]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    candidate_path = run_dir / "manifest.candidate.json"
    executor_prompt_path = prompt_path or run_dir / "executor_prompt.md"
    mapping = {
        "python": sys.executable,
        "project_root": str(project_root),
        "run_dir": str(run_dir),
        "result_dir": str(project_root / packet["run_context"]["result_dir"]),
        "task_packet_path": str(packet_path),
        "manifest_path": str(candidate_path),
        "manifest_candidate_path": str(candidate_path),
        "final_manifest_path": str(run_dir / "manifest.json"),
        "transcript_path": str(run_dir / "transcript.md"),
        "executor_brief_path": str(run_dir / "executor_brief.md"),
        "executor_prompt_path": str(executor_prompt_path),
        "adapter_contract_path": str(run_dir / "adapter_contract.json"),
        "manager_brief_path": str(run_dir / "manager_brief.json"),
        "manifest_repair_prompt_path": str(run_dir / "manifest_repair_prompt.md"),
        "worker_type": provider,
    }
    try:
        return shlex.split(template.format(**mapping))
    except KeyError as exc:
        missing = exc.args[0]
        raise RuntimeError(f"Launch template for provider={provider} referenced unknown placeholder {{{missing}}}.") from exc


def _run_provider_command(
    command: list[str],
    *,
    project_root: Path,
    env: dict[str, str],
    run_dir: Path,
    trace: dict[str, Any],
    phase: str,
    attempt: int,
    packet: dict,
    trace_started_monotonic: float,
) -> int:
    started = time.monotonic()
    attempt_record: dict[str, Any] = {
        "phase": phase,
        "attempt": attempt,
        "started_at": _utc_now(),
        "finished_at": None,
        "duration_seconds": None,
        "exit_code": None,
        "command": _redact_command(command),
        "stdout_line_count": 0,
        "bp_event_count": 0,
        "output_kind_counts": {},
        "first_output_at": None,
        "last_output_at": None,
        "last_output_lines": [],
    }
    trace["current_phase"] = phase
    trace.setdefault("provider_attempts", []).append(attempt_record)
    _write_trace(run_dir, trace)
    _append_output_timeline_event(
        run_dir,
        trace_started_monotonic=trace_started_monotonic,
        phase=phase,
        attempt=attempt,
        event_type="process_start",
        kind="system_event",
        metadata={"command": _redact_command(command)},
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"[wrapper] failed to launch provider command: {exc}", flush=True)
        attempt_record["finished_at"] = _utc_now()
        attempt_record["duration_seconds"] = round(time.monotonic() - started, 3)
        attempt_record["exit_code"] = 2
        attempt_record["launch_error"] = str(exc)
        trace["file_timeline"] = _collect_file_timeline(
            project_root, run_dir, packet, started_monotonic=trace_started_monotonic
        )
        _record_observation(trace, "provider_launch_failed", f"Provider command failed to launch: {exc}", severity="error")
        _write_trace(run_dir, trace)
        _append_output_timeline_event(
            run_dir,
            trace_started_monotonic=trace_started_monotonic,
            phase=phase,
            attempt=attempt,
            event_type="process_launch_error",
            kind="error",
            text=str(exc),
        )
        return 2
    last_line_monotonic = started
    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            if line:
                forwarded_line, truncated = _stdout_preview(line)
                print(forwarded_line, flush=True)
                line_seen_monotonic = time.monotonic()
                now = _utc_now()
                attempt_record["stdout_line_count"] += 1
                if attempt_record["first_output_at"] is None:
                    attempt_record["first_output_at"] = now
                attempt_record["last_output_at"] = now
                kind = _classify_output_line(line)
                if kind == "bp_event":
                    attempt_record["bp_event_count"] += 1
                _record_output_kind(attempt_record, kind, line)
                _append_output_timeline_event(
                    run_dir,
                    trace_started_monotonic=trace_started_monotonic,
                    phase=phase,
                    attempt=attempt,
                    event_type="stdout_line",
                    kind=kind,
                    text=forwarded_line,
                    line_number=attempt_record["stdout_line_count"],
                    gap_since_previous_seconds=round(line_seen_monotonic - last_line_monotonic, 3),
                    metadata={"truncated": truncated, "original_char_count": len(line)} if truncated else {},
                )
                last_line_monotonic = line_seen_monotonic
                lines = attempt_record["last_output_lines"]
                lines.append(_redact_text(line)[:1000])
                del lines[:-TRACE_LINE_LIMIT]
                if attempt_record["stdout_line_count"] == 1 or attempt_record["stdout_line_count"] % 25 == 0:
                    trace["file_timeline"] = _collect_file_timeline(
                        project_root, run_dir, packet, started_monotonic=trace_started_monotonic
                    )
                    _write_trace(run_dir, trace)
    return_code = process.wait()
    attempt_record["finished_at"] = _utc_now()
    attempt_record["duration_seconds"] = round(time.monotonic() - started, 3)
    attempt_record["exit_code"] = return_code
    attempt_record["final_silence_seconds"] = round(max(0.0, time.monotonic() - last_line_monotonic), 3)
    trace["file_timeline"] = _collect_file_timeline(project_root, run_dir, packet, started_monotonic=trace_started_monotonic)
    if return_code != 0:
        _record_observation(trace, "provider_nonzero_exit", f"{phase} attempt {attempt} exited with {return_code}.", severity="error")
    if attempt_record["duration_seconds"] and attempt_record["duration_seconds"] > 120:
        _record_observation(trace, "slow_provider_attempt", f"{phase} attempt {attempt} took {attempt_record['duration_seconds']} seconds.", severity="warning")
    _write_trace(run_dir, trace)
    _append_output_timeline_event(
        run_dir,
        trace_started_monotonic=trace_started_monotonic,
        phase=phase,
        attempt=attempt,
        event_type="process_exit",
        kind="system_event",
        metadata={"exit_code": return_code, "duration_seconds": attempt_record["duration_seconds"]},
        gap_since_previous_seconds=attempt_record["final_silence_seconds"],
    )
    return return_code


def _manifest_validation_errors(path: Path, *, packet: dict[str, Any] | None = None, project_root: Path | None = None) -> list[str]:
    if not path.exists():
        return [f"{path.name} is missing."]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name} is not valid JSON: {exc}"]
    try:
        manifest = Manifest.model_validate(payload)
    except ValidationError as exc:
        return [str(error) for error in exc.errors()]
    if not packet:
        return []

    errors: list[str] = []

    expected_outputs = packet.get("expected_outputs") if isinstance(packet.get("expected_outputs"), list) else []
    expected_by_role = {
        item.get("role"): item
        for item in expected_outputs
        if isinstance(item, dict) and item.get("role") and item.get("path_hint") and item.get("artifact_class")
    }
    created_by_role = {item.role: item for item in manifest.created_assets if item.role}
    missing_output_roles = sorted(set(expected_by_role) - set(created_by_role))
    if missing_output_roles:
        formatted = ", ".join(
            f"{role} ({expected_by_role[role]['path_hint']})" for role in missing_output_roles if role in expected_by_role
        )
        errors.append(f"{path.name} is missing created_assets for expected outputs: {formatted}")
    if project_root is not None:
        for role, asset in created_by_role.items():
            expected = expected_by_role.get(role)
            if not expected:
                continue
            output_path = project_root / asset.path
            if not output_path.exists():
                errors.append(f"Missing output file: {asset.path} ({role})")
                continue
            detected_class = detect_artifact_class(output_path)
            detected_format = detect_artifact_format(output_path)
            if detected_class != expected.get("artifact_class"):
                errors.append(
                    f"{path.name} output class mismatch for {role}: expected {expected.get('artifact_class')}, got {detected_class or 'unknown'}"
                )
            accepted_formats = list(expected.get("accepted_formats") or [])
            if accepted_formats and detected_format not in accepted_formats:
                errors.append(
                    f"{path.name} output format mismatch for {role}: expected one of {', '.join(accepted_formats)}, got {detected_format or 'unknown'}"
                )
    return errors


def _promote_candidate_manifest(*, run_dir: Path) -> list[str]:
    candidate_path = run_dir / "manifest.candidate.json"
    manifest_path = run_dir / "manifest.json"
    packet_path = run_dir / "task_packet.json"
    packet = None
    if packet_path.exists():
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            packet = None
    _sync_run_generated_scripts(run_dir=run_dir, packet=packet)
    if candidate_path.exists():
        errors = _manifest_validation_errors(candidate_path, packet=packet, project_root=run_dir.parent.parent)
        if errors:
            return errors
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        atomic_write_json(manifest_path, payload)
        return []
    if manifest_path.exists():
        return _manifest_validation_errors(manifest_path, packet=packet, project_root=run_dir.parent.parent)
    return [f"{candidate_path.name} is missing; manifest.json is missing."]


def _sync_run_generated_scripts(*, run_dir: Path, packet: dict[str, Any] | None) -> None:
    run_id = str((packet or {}).get("task_id") or run_dir.name).strip()
    if not run_id:
        return
    source_root = run_dir / "scripts" / "generated" / run_id
    if not source_root.exists():
        return
    project_root = run_dir.parent.parent
    target_root = project_root / "scripts" / "generated" / run_id
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_root)
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _has_blocking_dependency_issue(run_dir: Path) -> bool:
    issue_path = run_dir / "dependency_issue.json"
    if issue_path.exists():
        try:
            payload = json.loads(issue_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        issues = list(payload.get("issues") or [])
        if any(isinstance(issue, dict) and issue.get("metadata", {}).get("blocking", payload.get("blocking", True)) for issue in issues):
            return True
    brief_path = run_dir / "manager_brief.json"
    if not brief_path.exists():
        return False
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    issues = list(brief.get("dependency_issues") or [])
    if not issues:
        issues = [
            issue
            for issue in list(brief.get("issues") or [])
            if isinstance(issue, dict) and issue.get("metadata", {}).get("issue_kind") == "runtime_dependency_missing"
        ]
    return any(isinstance(issue, dict) and issue.get("metadata", {}).get("blocking", True) for issue in issues)


def _write_manifest_repair_prompt(*, run_dir: Path, packet: dict, errors: list[str], attempt: int) -> Path:
    prompt_path = run_dir / "manifest_repair_prompt.md"
    candidate_path = run_dir / "manifest.candidate.json"
    expected_outputs = packet.get("expected_outputs", [])
    lines = [
        f"You are repairing the manifest for Blueprint run {packet.get('task_id', '')}.",
        "",
        "Do not rerun analysis. Do not change result files, code artifacts, graph files, or input files.",
        f"Rewrite only this file: {candidate_path}",
        "",
        "The previous manifest candidate failed strict schema validation:",
    ]
    lines.extend(f"- {error}" for error in errors)
    missing_output_paths = []
    for item in packet.get("expected_outputs", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        path_hint = item.get("path_hint")
        if role and path_hint and any(path_hint in error and "created_assets" in error for error in errors):
            missing_output_paths.append((role, path_hint, item.get("artifact_class")))
    if missing_output_paths:
        lines.extend(
            [
                "",
                "Missing required outputs that must be present on disk and declared in created_assets:",
            ]
        )
        lines.extend(
            f"- {role}: {path_hint} ({output_type or 'unknown type'})"
            for role, path_hint, output_type in missing_output_paths
        )
    lines.extend(
        [
            "",
            "Required manifest JSON shape:",
            "```json",
            json.dumps(
                {
                    "run_id": packet.get("task_id"),
                    "status": "success",
                    "summary": "One concise summary sentence.",
                    "created_assets": [
                        {
                            "role": item.get("role"),
                            "path": item.get("path_hint"),
                            "label": item.get("label"),
                            "asset_id": item.get("asset_id"),
                            "description": "Short factual description.",
                        }
                        for item in expected_outputs
                    ],
                    "code_artifacts": [
                        {
                            "path": f"scripts/generated/{packet.get('task_id')}/analysis.R",
                            "language": "R",
                            "purpose": "Reproducible analysis script.",
                        }
                    ],
                    "validation_evidence": {
                        "input_conclusion": "Short factual note about the declared inputs and whether they were used.",
                    },
                    "commands_executed": ["brief command description"],
                    "metrics": {},
                    "key_findings": [],
                    "recommended_graph_updates": [],
                    "warnings": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
            "Strict rules:",
            "- status must be exactly one of: success, failed, partial.",
            "- Use created_assets, not outputs.",
            "- Include summary and commands_executed.",
            "- created_assets paths must point to files already produced under allowed result paths.",
            "- created_assets roles must match expected_outputs.role.",
            "- Output file class and format are detected from the file itself and must satisfy the contract.",
            "- Preserve existing metrics and key findings if they are factual.",
            "- validation_evidence.input_conclusion should summarize the declared inputs without repeating the full list.",
        ]
    )
    prompt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "progress_update",
                "stage": "manifest_repair_prompt",
                "progress": None,
                "message": f"Manifest schema validation failed; requesting repair attempt {attempt}.",
                "metadata": {"errors": errors[:5]},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return prompt_path


def _repair_manifest(
    *,
    template: str,
    provider: str,
    packet_path: Path,
    run_dir: Path,
    project_root: Path,
    env: dict[str, str],
    packet: dict,
    errors: list[str],
    attempt: int,
    trace: dict[str, Any],
    trace_started_monotonic: float,
) -> int:
    repair_prompt_path = _write_manifest_repair_prompt(run_dir=run_dir, packet=packet, errors=errors, attempt=attempt)
    repair_env = {
        **env,
        "BLUEPRINT_EXECUTOR_PROMPT": str(repair_prompt_path),
        "BLUEPRINT_MANIFEST_REPAIR_PROMPT": str(repair_prompt_path),
    }
    command = _render_launch_command(
        template,
        provider=provider,
        packet_path=packet_path,
        run_dir=run_dir,
        project_root=project_root,
        prompt_path=repair_prompt_path,
    )
    return _run_provider_command(
        command,
        project_root=project_root,
        env=repair_env,
        run_dir=run_dir,
        trace=trace,
        phase="manifest_repair",
        attempt=attempt,
        packet=packet,
        trace_started_monotonic=trace_started_monotonic,
    )


def main() -> int:
    args = _parse_args()
    provider = args.provider
    packet_path = Path(args.task_packet)
    run_dir = Path(args.run_dir)
    project_root = Path(args.project_root)
    env = os.environ.copy()
    template = env.get("BLUEPRINT_AGENT_LAUNCH_TEMPLATE", "").strip()
    if not template:
        print(f"[wrapper] missing launch template for provider={provider}", flush=True)
        return 2

    packet = _load_task_packet(packet_path)
    started = time.monotonic()
    trace = _new_trace(provider=provider, packet=packet, run_dir=run_dir, project_root=project_root, template=template)
    _write_trace(run_dir, trace)
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "progress_update",
                "stage": "dispatch",
                "progress": 1,
                "message": f"Dispatching {provider} wrapper with unified executor prompt.",
                "metadata": {"provider": provider},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        command = _render_launch_command(template, provider=provider, packet_path=packet_path, run_dir=run_dir, project_root=project_root)
    except RuntimeError as exc:
        print(f"[wrapper] {exc}", flush=True)
        trace["status"] = "failed"
        trace["current_phase"] = "launch_template_error"
        trace["finished_at"] = _utc_now()
        trace["total_duration_seconds"] = round(time.monotonic() - started, 3)
        _record_observation(trace, "launch_template_error", str(exc), severity="error")
        _write_trace(run_dir, trace)
        return 2
    return_code = _run_provider_command(
        command,
        project_root=project_root,
        env=env,
        run_dir=run_dir,
        trace=trace,
        phase="initial_provider",
        attempt=1,
        packet=packet,
        trace_started_monotonic=started,
    )
    if _has_blocking_dependency_issue(run_dir):
        trace["status"] = "failed"
        trace["current_phase"] = "runtime_dependency_missing"
        trace["finished_at"] = _utc_now()
        trace["total_duration_seconds"] = round(time.monotonic() - started, 3)
        _record_observation(
            trace,
            "runtime_dependency_missing",
            "Provider reported missing required runtime dependencies; manifest repair was skipped.",
            severity="error",
        )
        trace["file_timeline"] = _collect_file_timeline(project_root, run_dir, packet, started_monotonic=started)
        _write_trace(run_dir, trace)
        return 3
    if return_code == 0:
        validation_errors = _promote_candidate_manifest(run_dir=run_dir)
        trace.setdefault("manifest_validation", []).append(
            {
                "phase": "initial_provider",
                "attempt": 1,
                "checked_at": _utc_now(),
                "status": "failed" if validation_errors else "passed",
                "errors": validation_errors[:10],
            }
        )
        if validation_errors:
            _record_observation(trace, "manifest_validation_failed", "Initial manifest candidate failed schema validation.", severity="warning")
        trace["file_timeline"] = _collect_file_timeline(project_root, run_dir, packet, started_monotonic=started)
        _write_trace(run_dir, trace)
        for attempt in range(1, MAX_MANIFEST_REPAIR_ATTEMPTS + 1):
            if not validation_errors:
                break
            repair_code = _repair_manifest(
                template=template,
                provider=provider,
                packet_path=packet_path,
                run_dir=run_dir,
                project_root=project_root,
                env=env,
                packet=packet,
                errors=validation_errors,
                attempt=attempt,
                trace=trace,
                trace_started_monotonic=started,
            )
            if repair_code != 0:
                return_code = repair_code
                break
            validation_errors = _promote_candidate_manifest(run_dir=run_dir)
            trace.setdefault("manifest_validation", []).append(
                {
                    "phase": "manifest_repair",
                    "attempt": attempt,
                    "checked_at": _utc_now(),
                    "status": "failed" if validation_errors else "passed",
                    "errors": validation_errors[:10],
                }
            )
            trace["file_timeline"] = _collect_file_timeline(project_root, run_dir, packet, started_monotonic=started)
            _write_trace(run_dir, trace)
        if return_code == 0 and validation_errors:
            print("[wrapper] manifest schema validation failed after repair attempts:", flush=True)
            for error in validation_errors:
                print(f"[wrapper] - {error}", flush=True)
            return_code = 1
            _record_observation(trace, "manifest_repair_exhausted", "Manifest schema validation failed after repair attempts.", severity="error")
    trace["status"] = "success" if return_code == 0 else "failed"
    trace["current_phase"] = "complete"
    trace["finished_at"] = _utc_now()
    trace["total_duration_seconds"] = round(time.monotonic() - started, 3)
    trace["file_timeline"] = _collect_file_timeline(project_root, run_dir, packet, started_monotonic=started)
    if not (run_dir / "manifest.json").exists():
        _record_observation(trace, "manifest_missing", "manifest.json was not produced.", severity="error")
    _write_trace(run_dir, trace)
    print(
        "BP_EVENT "
        + json.dumps(
            {
                "type": "progress_update",
                "stage": "dispatch_complete",
                "progress": 100 if return_code == 0 else None,
                "message": f"{provider} wrapper finished with exit code {return_code}.",
                "metadata": {"provider": provider, "input_assets": len(packet.get('input_assets', []))},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
