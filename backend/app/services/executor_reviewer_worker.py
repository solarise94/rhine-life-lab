from __future__ import annotations

import json
from http import client
from pathlib import Path
from posixpath import normpath
import py_compile
import re
from typing import Any
from urllib import error, request

from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings
from app.models.runs import Manifest, TaskPacket, ValidationIssue
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanningError
from app.services.utils import atomic_write_json, resolve_within, utc_now


MAX_REVIEW_PREVIEW_BYTES = 16_000
MAX_REVIEW_CODE_BYTES = 200_000
MAX_REVIEW_EXECUTION_RECORD_BYTES = 80_000
DEFAULT_MAX_REVIEW_TURNS = 24


REVIEWER_SYSTEM_PROMPT = """You are Blueprint's read-only Executor Reviewer agent.

Your job is to audit the executor's execution method, preserved scripts, and self-reported summary.
Decide whether the run is backed by real, contract-following execution evidence.

Rules:
- You may only inspect files through the provided tools.
- Do not propose card, graph, or asset mutations.
- Do not create or modify files.
- Use submit_executor_review exactly once when you have enough evidence.
- If submit_executor_review returns a protocol error, fix the listed fields and call submit_executor_review again.
- Review the script contract, not the scientific merit of the biological/statistical conclusion.
- Treat agent self-reports as evidence: if the executor admits missing dependencies, shortcuts, skipped inputs, failed commands, or fallback/synthetic outputs, reflect that in the verdict.
- Fail if the executor appears to fake outputs, skip declared inputs, use placeholder/synthetic/demo logic, or implement logic inconsistent with the task.
- Warn for incomplete evidence that does not prove failure.
- Prefer the shortest successful review path: list_review_files, inspect task/manifest/execution report/script evidence first, optionally inspect one representative output shape, then submit_executor_review.
- Do not inspect raw input data unless script/manifest evidence is insufficient to judge whether the declared input path was used.
- Do not deeply read every table, report, figure, SVG, or raw data asset. Output assets are secondary evidence for existence/shape only.
- Do not make environment policy decisions. Missing packages, missing R/Python runtime, or blocked network are executor capability findings, not reviewer remediation work.
- Do not keep exploring after you already have enough evidence for a verdict.
- If the previous tool_result reports a protocol_error, your next tool call must correct that protocol error.
- inspected_files in submit_executor_review must contain real file paths you actually inspected.
"""


class ExecutorReviewIssue(BaseModel):
    severity: str = Field(pattern="^(info|warning|error)$")
    code: str
    message: str
    path: str | None = None
    repair_hint: str | None = None


class ExecutorReviewVerdict(BaseModel):
    verdict: str = Field(pattern="^(pass|warn|fail)$")
    summary: str
    issues: list[ExecutorReviewIssue] = Field(default_factory=list)
    repair_hints: list[str] = Field(default_factory=list)
    inspected_files: list[str] = Field(default_factory=list)


class ExecutorReviewerWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def review(
        self,
        *,
        root: Path,
        packet: TaskPacket,
        manifest: Manifest,
        deterministic_issues: list[ValidationIssue],
    ) -> dict[str, Any]:
        api_key = self.settings.deepseek_api_key.get_secret_value() if self.settings.deepseek_api_key else ""
        if not api_key:
            return {
                "verdict": "warn",
                "summary": "Reviewer skipped because BLUEPRINT_DEEPSEEK_API_KEY is not configured.",
                "issues": [],
                "mode": "reviewer_worker_skipped",
            }

        trace_jsonl = root / "runs" / packet.task_id / "reviewer_trace.jsonl"
        trace_json = root / "runs" / packet.task_id / "reviewer_trace.json"
        turns: list[dict[str, Any]] = []
        summary = {
            "turns": 0,
            "tool_calls_total": 0,
            "submit_attempts": 0,
            "submit_schema_failures": 0,
            "missing_tool_call_turns": 0,
            "last_protocol_error_code": None,
            "model": self.settings.reviewer_model or self.settings.manager_model,
        }
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            self._initial_context(root, packet, manifest, deterministic_issues),
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
            }
        ]
        tools = self._tools()
        max_review_turns = self._max_review_turns()
        final_submit_warning_sent = False
        for turn_index in range(max_review_turns):
            if not final_submit_warning_sent and turn_index >= max(1, max_review_turns - 3):
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "review_protocol": "final_submit_required",
                                        "message": (
                                            "You are near the reviewer turn limit. Stop exploring. "
                                            "Your next tool call must be submit_executor_review using only the evidence already inspected."
                                        ),
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                )
                final_submit_warning_sent = True
            response_payload = self._post_messages(messages=messages, tools=tools, api_key=api_key)
            tool_uses = self._tool_uses(response_payload)
            turn_record: dict[str, Any] = {
                "turn_index": turn_index + 1,
                "request_model": self.settings.reviewer_model or self.settings.manager_model,
                "assistant_content": response_payload.get("content", []),
                "tool_uses": [],
                "tool_results": [],
                "protocol_errors": [],
                "final_review_candidate": None,
                "accepted_final_review": False,
                "timestamp": utc_now(),
            }
            if not tool_uses:
                summary["missing_tool_call_turns"] += 1
                protocol_error = self._protocol_error(
                    code="missing_tool_call",
                    message="Reviewer must use tools and finish with submit_executor_review.",
                    repair_hint="Inspect evidence if needed, then call submit_executor_review with verdict, summary, issues, repair_hints, and inspected_files.",
                )
                turn_record["protocol_errors"].append(protocol_error["protocol_error"])
                messages.append({"role": "assistant", "content": response_payload.get("content", [])})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(protocol_error, ensure_ascii=False),
                            }
                        ],
                    }
                )
                turns.append(turn_record)
                self._write_trace(trace_jsonl, trace_json, turns, summary)
                continue
            messages.append({"role": "assistant", "content": response_payload.get("content", [])})
            result_blocks = []
            final: dict[str, Any] | None = None
            for tool_use in tool_uses:
                turn_record["tool_uses"].append(
                    {
                        "tool_use_id": tool_use.get("id"),
                        "name": tool_use.get("name"),
                        "input": tool_use.get("input"),
                    }
                )
                summary["tool_calls_total"] += 1
                if tool_use.get("name") == "submit_executor_review":
                    summary["submit_attempts"] += 1
                    verdict, protocol_error = self._validate_final_review(tool_use)
                    if verdict is not None:
                        if final is None:
                            final = verdict
                            turn_record["final_review_candidate"] = verdict
                            turn_record["accepted_final_review"] = True
                        result = {"ok": True, "accepted": True, "message": "Executor review accepted."}
                    else:
                        result = protocol_error
                        turn_record["protocol_errors"].append(protocol_error.get("protocol_error"))
                        summary["submit_schema_failures"] += 1
                        summary["last_protocol_error_code"] = protocol_error.get("protocol_error", {}).get("code")
                else:
                    result = self._handle_tool(root, packet, manifest, tool_use)
                    if isinstance(result, dict) and result.get("protocol_error"):
                        turn_record["protocol_errors"].append(result.get("protocol_error"))
                        summary["last_protocol_error_code"] = result.get("protocol_error", {}).get("code")
                turn_record["tool_results"].append(
                    {
                        "tool_use_id": tool_use["id"],
                        "ok": result.get("ok") if isinstance(result, dict) else None,
                        "content": result,
                        "error": result.get("error") if isinstance(result, dict) else None,
                    }
                )
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": result_blocks})
            turns.append(turn_record)
            self._write_trace(trace_jsonl, trace_json, turns, summary)
            if final is not None:
                final["mode"] = "reviewer_worker"
                final["turns"] = len(turns)
                final["reviewer"] = {
                    **summary,
                    "turns": len(turns),
                    "trace_path": f"runs/{packet.task_id}/reviewer_trace.jsonl",
                }
                self._write_trace(trace_jsonl, trace_json, turns, summary)
                return final

        return {
            "verdict": "fail",
            "summary": f"Reviewer failed to submit a valid executor review within {max_review_turns} tool turns.",
            "issues": [
                {
                    "severity": "error",
                    "code": "reviewer_protocol_not_satisfied",
                    "message": "Reviewer did not complete the submit_executor_review tool contract.",
                    "repair_hint": "Retry review; if repeated, inspect the reviewer model/tool configuration.",
                }
            ],
            "mode": "reviewer_worker_max_turns",
            "reviewer": {
                **summary,
                "turns": len(turns),
                "trace_path": f"runs/{packet.task_id}/reviewer_trace.jsonl",
            },
        }

    def _post_messages(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]], api_key: str) -> dict[str, Any]:
        configured_model = self.settings.reviewer_model or self.settings.manager_model
        resolved_model = DeepSeekManagerPlanner.resolve_tool_model(configured_model)
        payload = {
            "model": resolved_model,
            "max_tokens": self.settings.reviewer_max_tokens or self.settings.manager_max_tokens,
            "temperature": self.settings.manager_temperature,
            "system": REVIEWER_SYSTEM_PROMPT,
            "messages": messages,
            "tools": tools,
            "tool_choice": {"type": "any"},
        }
        http_request = request.Request(
            f"{self.settings.deepseek_api_base_url.rstrip('/')}/v1/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with request.urlopen(http_request, timeout=min(self.settings.manager_timeout_seconds, 90)) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ManagerPlanningError(
                DeepSeekManagerPlanner._build_http_error_message(
                    status_code=exc.code,
                    detail=detail,
                    configured_model=configured_model,
                    resolved_model=resolved_model,
                )
            ) from exc
        except (error.URLError, TimeoutError, OSError, client.HTTPException) as exc:
            raise ManagerPlanningError(f"Reviewer DeepSeek request failed: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManagerPlanningError("Reviewer DeepSeek returned invalid JSON at the HTTP layer.") from exc

    def _max_review_turns(self) -> int:
        return max(1, int(self.settings.reviewer_max_turns or DEFAULT_MAX_REVIEW_TURNS))

    @staticmethod
    def _write_trace(trace_jsonl: Path, trace_json: Path, turns: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        trace_jsonl.parent.mkdir(parents=True, exist_ok=True)
        trace_jsonl.write_text(
            "".join(json.dumps(turn, ensure_ascii=False) + "\n" for turn in turns),
            encoding="utf-8",
        )
        atomic_write_json(trace_json, {"summary": {**summary, "turns": len(turns)}, "turns": turns})

    @staticmethod
    def _tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "list_review_files",
                "description": "List files that the reviewer is allowed to inspect.",
                "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "read_review_file",
                "description": "Read a UTF-8 preview of an allowed review file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "inspect_table",
                "description": "Return row/column counts and headers for an allowed TSV/CSV output or input file.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "analyze_code_artifact",
                "description": "Return read-only static evidence for a declared code artifact: syntax status, referenced declared paths, and suspicious placeholder markers.",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "submit_executor_review",
                "description": "Submit the final executor review verdict.",
                "input_schema": ExecutorReviewVerdict.model_json_schema(),
            },
        ]

    @staticmethod
    def _tool_uses(response_payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            block
            for block in response_payload.get("content", [])
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]

    @staticmethod
    def _validate_final_review(tool_use: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        tool_input = tool_use.get("input")
        if not isinstance(tool_input, dict):
            return None, ExecutorReviewerWorker._protocol_error(
                code="invalid_submit_executor_review_input",
                message="submit_executor_review input must be a JSON object.",
                repair_hint="Call submit_executor_review with an object matching the declared input_schema.",
            )
        try:
            return ExecutorReviewVerdict.model_validate(tool_input).model_dump(), {}
        except ValidationError as exc:
            return None, ExecutorReviewerWorker._protocol_error(
                code="invalid_submit_executor_review_schema",
                message="submit_executor_review input failed schema validation.",
                schema_errors=exc.errors(include_url=False),
                repair_hint="Fix the listed fields and call submit_executor_review again.",
            )

    @staticmethod
    def _protocol_error(
        *,
        code: str,
        message: str,
        repair_hint: str,
        schema_errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "protocol_error": {
                "code": code,
                "message": message,
                "repair_hint": repair_hint,
            },
        }
        if schema_errors is not None:
            payload["protocol_error"]["schema_errors"] = schema_errors
        return payload

    def _handle_tool(self, root: Path, packet: TaskPacket, manifest: Manifest, tool_use: dict[str, Any]) -> dict[str, Any]:
        name = tool_use.get("name")
        tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
        try:
            if name == "list_review_files":
                return {"ok": True, "files": self._allowed_review_files(packet, manifest)}
            if name == "read_review_file":
                return self._read_review_file(root, packet, manifest, str(tool_input.get("path") or ""))
            if name == "inspect_table":
                return self._inspect_table(root, packet, manifest, str(tool_input.get("path") or ""))
            if name == "check_python_code":
                return self._check_python_code(root, packet, manifest, str(tool_input.get("path") or ""))
            if name == "analyze_code_artifact":
                return self._analyze_code_artifact(root, packet, manifest, str(tool_input.get("path") or ""))
            return {"ok": False, "error": f"Unknown reviewer tool: {name}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _read_review_file(self, root: Path, packet: TaskPacket, manifest: Manifest, relative_path: str) -> dict[str, Any]:
        path = self._resolve_allowed(root, packet, manifest, relative_path)
        if not path.is_file():
            return {"ok": False, "error": "Path is not a file.", "path": relative_path}
        preview_limit = self._review_preview_limit(packet, manifest, relative_path)
        data = path.read_bytes()[:preview_limit]
        if b"\x00" in data:
            return {
                "ok": False,
                "error": "Path appears to be binary; use structured inspection tools or manifest metadata instead.",
                "path": relative_path,
                "size_bytes": path.stat().st_size,
            }
        text = data.decode("utf-8", errors="replace")
        replacement_ratio = text.count("\ufffd") / max(len(text), 1)
        if replacement_ratio > 0.05:
            return {
                "ok": False,
                "error": "Path is not valid UTF-8 text; use structured inspection tools or manifest metadata instead.",
                "path": relative_path,
                "size_bytes": path.stat().st_size,
            }
        return {
            "ok": True,
            "path": relative_path,
            "size_bytes": path.stat().st_size,
            "truncated": path.stat().st_size > preview_limit,
            "preview_limit_bytes": preview_limit,
            "content": text,
        }

    @staticmethod
    def _review_preview_limit(packet: TaskPacket, manifest: Manifest, relative_path: str) -> int:
        declared_code_paths = {item.path for item in manifest.code_artifacts}
        if relative_path in declared_code_paths:
            return MAX_REVIEW_CODE_BYTES
        execution_record_paths = {
            f"runs/{packet.task_id}/task_packet.json",
            f"runs/{packet.task_id}/manifest.json",
            f"runs/{packet.task_id}/adapter_contract.json",
            f"runs/{packet.task_id}/manager_brief.json",
            f"runs/{packet.task_id}/commands.log",
            f"runs/{packet.task_id}/transcript.md",
            f"runs/{packet.task_id}/filesystem_audit.json",
            f"runs/{packet.task_id}/sandbox_plan.json",
        }
        if relative_path in execution_record_paths:
            return MAX_REVIEW_EXECUTION_RECORD_BYTES
        return MAX_REVIEW_PREVIEW_BYTES

    def _inspect_table(self, root: Path, packet: TaskPacket, manifest: Manifest, relative_path: str) -> dict[str, Any]:
        path = self._resolve_allowed(root, packet, manifest, relative_path)
        if not path.is_file():
            return {"ok": False, "error": "Path is not a file.", "path": relative_path}
        header: list[str] = []
        rows = 0
        delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for line_number, line in enumerate(handle):
                parts = line.rstrip("\n").split(delimiter)
                if line_number == 0:
                    header = parts
                else:
                    rows += 1
        return {"ok": True, "path": relative_path, "columns": len(header), "rows": rows, "header": header[:50]}

    def _check_python_code(self, root: Path, packet: TaskPacket, manifest: Manifest, relative_path: str) -> dict[str, Any]:
        path = self._resolve_allowed(root, packet, manifest, relative_path)
        if path.suffix.lower() != ".py":
            return {"ok": False, "error": "Only .py code artifacts can be py_compile checked.", "path": relative_path}
        py_compile.compile(str(path), doraise=True)
        return {"ok": True, "path": relative_path, "check": "py_compile"}

    def _analyze_code_artifact(self, root: Path, packet: TaskPacket, manifest: Manifest, relative_path: str) -> dict[str, Any]:
        path = self._resolve_allowed(root, packet, manifest, relative_path)
        declared_code_paths = {item.path for item in manifest.code_artifacts}
        if relative_path not in declared_code_paths:
            return {"ok": False, "error": "Path is not a declared code artifact.", "path": relative_path}
        if not path.is_file():
            return {"ok": False, "error": "Path is not a file.", "path": relative_path}

        text = path.read_text(encoding="utf-8", errors="replace")
        declared_paths = [item.path for item in [*packet.input_assets, *manifest.created_assets]]
        referenced_paths = [item for item in declared_paths if item and item in text]
        suspicious_markers = [
            marker
            for marker in ["placeholder", "synthetic", "demo", "term_1", "term_2", "fake", "dummy"]
            if re.search(rf"\b{re.escape(marker)}\b", text, flags=re.IGNORECASE)
        ]
        syntax: dict[str, Any] = {"checked": False}
        if path.suffix.lower() == ".py":
            try:
                py_compile.compile(str(path), doraise=True)
                syntax = {"checked": True, "ok": True, "check": "py_compile"}
            except py_compile.PyCompileError as exc:
                syntax = {"checked": True, "ok": False, "error": str(exc)}

        return {
            "ok": True,
            "path": relative_path,
            "size_bytes": path.stat().st_size,
            "syntax": syntax,
            "declared_input_paths": [item.path for item in packet.input_assets],
            "declared_output_paths": [item.path for item in manifest.created_assets],
            "referenced_declared_paths": referenced_paths,
            "missing_declared_input_references": [item.path for item in packet.input_assets if item.path not in referenced_paths],
            "missing_declared_output_references": [item.path for item in manifest.created_assets if item.path not in referenced_paths],
            "suspicious_markers": sorted(set(suspicious_markers)),
        }

    def _resolve_allowed(self, root: Path, packet: TaskPacket, manifest: Manifest, relative_path: str) -> Path:
        allowed = set(self._allowed_review_files(packet, manifest))
        if relative_path not in allowed:
            raise ValueError(f"Reviewer is not allowed to inspect path: {relative_path}")
        return resolve_within(root, relative_path)

    @classmethod
    def _allowed_review_files(cls, packet: TaskPacket, manifest: Manifest) -> list[str]:
        files = {
            f"runs/{packet.task_id}/task_packet.json",
            f"runs/{packet.task_id}/manifest.json",
            f"runs/{packet.task_id}/adapter_contract.json",
            f"runs/{packet.task_id}/manager_brief.json",
            f"runs/{packet.task_id}/commands.log",
            f"runs/{packet.task_id}/transcript.md",
            f"runs/{packet.task_id}/filesystem_audit.json",
            f"runs/{packet.task_id}/sandbox_plan.json",
        }
        files.update(path for path in (cls._clean_relative_path(item.path) for item in packet.input_assets) if path)
        files.update(
            path
            for path in (cls._allowed_created_asset_path(packet, item.role, item.path) for item in manifest.created_assets)
            if path
        )
        files.update(
            path
            for path in (cls._allowed_code_artifact_path(packet, item.path) for item in manifest.code_artifacts)
            if path
        )
        return sorted(files)

    @staticmethod
    def _clean_relative_path(path: str) -> str | None:
        value = path.strip().replace("\\", "/")
        if not value or value.startswith("/"):
            return None
        normalized = normpath(value)
        if normalized in {"", "."} or normalized.startswith("../") or normalized == "..":
            return None
        return normalized

    @classmethod
    def _allowed_created_asset_path(cls, packet: TaskPacket, role: str, path: str) -> str | None:
        normalized = cls._clean_relative_path(path)
        if normalized is None:
            return None
        expected = next((item for item in packet.expected_outputs if item.role == role), None)
        if expected is None:
            return None
        expected_paths = [cls._clean_relative_path(item) for item in expected.allowed_path_hints()]
        if normalized in {item for item in expected_paths if item}:
            return normalized
        allowed_prefixes = tuple(prefix for prefix in (cls._clean_allowed_prefix(item) for item in packet.allowed_paths) if prefix)
        if allowed_prefixes and normalized.startswith(allowed_prefixes):
            return normalized
        return None

    @classmethod
    def _allowed_code_artifact_path(cls, packet: TaskPacket, path: str) -> str | None:
        normalized = cls._clean_relative_path(path)
        if normalized is None:
            return None
        if normalized.startswith((f"scripts/generated/{packet.task_id}/", f"runs/{packet.task_id}/")):
            return normalized
        return None

    @classmethod
    def _clean_allowed_prefix(cls, path: str) -> str | None:
        normalized = cls._clean_relative_path(path)
        if normalized is None:
            return None
        return normalized if normalized.endswith("/") else f"{normalized}/"

    def _initial_context(
        self,
        root: Path,
        packet: TaskPacket,
        manifest: Manifest,
        deterministic_issues: list[ValidationIssue],
    ) -> dict[str, Any]:
        return {
            "review_goal": (
                "Audit whether the executor's execution method, preserved scripts, and self-report prove "
                "that the card contract was followed."
            ),
            "review_scope": {
                "primary": [
                    "task contract and expected outputs",
                    "manifest summary, commands, transcript, filesystem audit, and manager brief",
                    "declared code artifacts and their references to declared inputs/outputs",
                    "executor self-reported warnings, missing dependencies, failed commands, shortcuts, or fallbacks",
                ],
                "secondary": [
                    "one representative table/header or output existence check when needed",
                    "created asset metadata from manifest",
                ],
                "out_of_scope": [
                    "deep inspection of raw input data",
                    "scientific judgment of whether the biological/statistical conclusion is interesting",
                    "environment remediation or dependency installation decisions",
                    "reading every report, figure, SVG, table, or raw file",
                ],
            },
            "review_strategy": [
                "Call list_review_files first.",
                "Select a minimal evidence set before reading: manifest/task packet, agent execution report, declared code artifacts, and at most one representative output shape if needed.",
                "Read execution records and scripts before output assets.",
                "Use analyze_code_artifact for declared scripts, then read the script only if static evidence is insufficient.",
                "Use inspect_table for output shape instead of reading large output tables when possible.",
                "Do not read raw input data unless script evidence cannot show whether declared inputs were referenced.",
                "As soon as you can justify a verdict, call submit_executor_review.",
                "If a tool_result reports a protocol_error, fix that error on the next tool call.",
            ],
            "verdict_guidance": [
                "pass: preserved script and execution records credibly show the contract was executed and expected outputs were produced.",
                "warn: evidence is incomplete or environment/runtime issues limited confidence, but there is no clear contract violation.",
                "fail: script/report evidence shows fake, placeholder, synthetic, skipped, or materially non-contract execution.",
            ],
            "required_submit_fields": ["verdict", "summary", "issues", "repair_hints", "inspected_files"],
            "task": {
                "run_id": packet.task_id,
                "card_id": packet.card_id,
                "card_title": packet.card_title,
                "card_status_at_launch": packet.card_status,
                "goal": packet.goal,
                "card_inputs": [item.model_dump() for item in packet.card_inputs],
                "card_outputs": [item.model_dump() for item in packet.card_outputs],
                "expected_outputs": [item.model_dump() for item in packet.expected_outputs],
                "input_assets": [item.model_dump() for item in packet.input_assets],
                "constraints": packet.constraints,
                "execution_policy": packet.execution_policy.model_dump(),
                "worker_instructions": packet.worker_instructions,
                "run_context": packet.run_context.model_dump() if packet.run_context else None,
                "executor_context": packet.executor_context.model_dump() if packet.executor_context else None,
                "manager_reporting_contract": packet.manager_reporting_contract.model_dump() if packet.manager_reporting_contract else None,
            },
            "manifest_summary": {
                "summary": manifest.summary,
                "created_assets": [item.model_dump() for item in manifest.created_assets],
                "code_artifacts": [item.model_dump() for item in manifest.code_artifacts],
                "commands_executed": manifest.commands_executed,
                "metrics": manifest.metrics,
                "warnings": manifest.warnings,
            },
            "deterministic_issues": [item.model_dump() for item in deterministic_issues],
            "allowed_files": self._allowed_review_files(packet, manifest),
            "path_scope": "All tool paths are project-root-relative; absolute project root is intentionally hidden from the reviewer.",
        }
