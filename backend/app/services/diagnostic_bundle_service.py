from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.services.app_config_service import AppConfigService
from app.services.project_service import ProjectService
from app.services.utils import utc_now


SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_-]+|gh[opsu]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)")
HOME_PATH_RE = re.compile(r"/home/[^/\s]+")


class DiagnosticBundleService:
    def __init__(self, project_service: ProjectService, app_config_service: AppConfigService) -> None:
        self.project_service = project_service
        self.app_config_service = app_config_service

    def build_bundle(self, project_id: str, *, max_runs: int = 8) -> dict[str, Any]:
        project_root = self.project_service.project_path(project_id)
        snapshot = self.project_service.get_project_snapshot(project_id)
        files_payload = self._list_run_file_payload(project_id, max_runs=max_runs)
        bundle_dir = project_root / "reports" / "diagnostics"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now().replace(":", "").replace("-", "")
        bundle_name = f"{project_id}_diagnostic_bundle_{timestamp}.zip"
        bundle_path = bundle_dir / bundle_name

        with tempfile.TemporaryDirectory(prefix="diagnostic-bundle-") as tmp_dir_raw:
            tmp_dir = Path(tmp_dir_raw)
            payload_root = tmp_dir / f"{project_id}_diagnostic_bundle"
            payload_root.mkdir(parents=True, exist_ok=True)
            self._write_json(payload_root / "manifest.json", self._manifest(project_id, snapshot, files_payload))
            self._write_json(payload_root / "project_snapshot.json", self._sanitize(snapshot))
            self._write_json(payload_root / "app_settings_summary.json", self._app_settings_summary())
            self._write_json(payload_root / "sessions.json", self._chat_sessions(project_id))
            self._write_json(payload_root / "recent_errors.json", self._recent_errors(snapshot, files_payload))
            self._write_run_files(payload_root / "runs", project_root, files_payload)
            self._zip_dir(payload_root, bundle_path)

        return {
            "path": str(bundle_path.relative_to(project_root)),
            "download_url": f"/api/projects/{project_id}/diagnostics/download?path={bundle_path.relative_to(project_root).as_posix()}",
            "created_at": utc_now(),
            "run_count": len(files_payload["runs"]),
            "session_count": len(self._chat_sessions(project_id).get("items", [])),
        }

    def _manifest(self, project_id: str, snapshot: dict[str, Any], files_payload: dict[str, Any]) -> dict[str, Any]:
        summary = snapshot["summary"]
        return {
            "kind": "project_diagnostic_bundle",
            "project_id": project_id,
            "created_at": utc_now(),
            "project": {
                "name": summary.name,
                "status": summary.status,
                "current_goal": summary.current_goal,
                "card_counts": summary.card_counts,
                "result_counts": summary.result_counts,
            },
            "includes": {
                "project_snapshot": True,
                "app_settings_summary": True,
                "chat_sessions": True,
                "recent_errors": True,
                "run_directories": [item["run_id"] for item in files_payload["runs"]],
            },
            "redaction": {
                "api_keys": True,
                "tokens": True,
                "home_paths": True,
            },
        }

    def _app_settings_summary(self) -> dict[str, Any]:
        public = self.app_config_service.get_public_settings()
        secret = self.app_config_service.get_secret_settings()
        return self._sanitize(
            {
                "public": public,
                "effective": {
                    "manager_model": secret.get("manager_model"),
                    "executor_model": secret.get("executor_model"),
                    "reviewer_model": secret.get("reviewer_model"),
                    "library_summarizer_model": secret.get("library_summarizer_model"),
                    "manager_websearch_enabled": secret.get("manager_websearch_enabled"),
                    "deepseek_api_base_url": secret.get("deepseek_api_base_url"),
                    "pi_deepseek_base_url": secret.get("pi_deepseek_base_url"),
                    "tavily_base_url": secret.get("tavily_base_url"),
                },
            }
        )

    def _chat_sessions(self, project_id: str) -> dict[str, Any]:
        sessions = self.project_service.graph_store(project_id).load_chat_sessions()
        payload = {
            "items": [
                {
                    "session_id": item.session_id,
                    "summary": item.summary,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "messages": [message.model_dump() for message in item.messages],
                }
                for item in sessions
            ]
        }
        return self._sanitize(payload)

    def _recent_errors(self, snapshot: dict[str, Any], files_payload: dict[str, Any]) -> dict[str, Any]:
        failed_cards = []
        for card in snapshot["cards"]:
            if getattr(card, "status", None) in {"failed", "cancelled", "needs_review", "reviewing"}:
                failed_cards.append(
                    {
                        "card_id": card.card_id,
                        "title": card.title,
                        "status": card.status,
                        "summary": card.summary,
                        "linked_runs": card.linked_runs,
                    }
                )
        run_errors = []
        for run in files_payload["runs"]:
            status = str(run.get("status") or "")
            if status not in {"failed", "cancelled", "reviewing", "needs_review"}:
                continue
            run_errors.append(
                {
                    "run_id": run["run_id"],
                    "card_id": run.get("card_id"),
                    "status": status,
                    "error_hint": run.get("error_hint"),
                    "files": [file["path"] for file in run["files"]],
                }
            )
        return self._sanitize({"cards": failed_cards, "runs": run_errors})

    def _list_run_file_payload(self, project_id: str, *, max_runs: int) -> dict[str, Any]:
        project_root = self.project_service.project_path(project_id)
        graph = self.project_service.graph_store(project_id).load_graph()
        runs = sorted(
            graph.runs,
            key=lambda item: (item.finished_at or item.started_at or "", item.run_id),
            reverse=True,
        )[:max_runs]
        entries = []
        for run in runs:
            run_dir = project_root / "runs" / run.run_id
            files = []
            for filename in (
                "events.json",
                "transcript.md",
                "commands.log",
                "filesystem_audit.json",
                "executor_validation.json",
                "review_context.json",
                "reviewer_trace.json",
                "reviewer_trace.jsonl",
                "agent_trace.json",
                "agent_output_timeline.jsonl",
                "manifest.candidate.json",
                "manifest.json",
                "dependency_issue.json",
                "runtime_approvals.json",
                "sandbox_plan.json",
                "task_packet.json",
                "manager_brief.json",
                "executor_brief.md",
                "executor_prompt.md",
                "adapter_contract.json",
            ):
                path = run_dir / filename
                if path.exists() and path.is_file():
                    files.append({"path": path.relative_to(project_root).as_posix(), "filename": filename})
            entries.append(
                {
                    "run_id": run.run_id,
                    "card_id": run.card_id,
                    "status": run.status,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "error_hint": getattr(run, "error", None) or getattr(run, "summary", None),
                    "files": files,
                }
            )
        return {"runs": entries}

    def _write_run_files(self, target_root: Path, project_root: Path, files_payload: dict[str, Any]) -> None:
        for run in files_payload["runs"]:
            run_target = target_root / run["run_id"]
            run_target.mkdir(parents=True, exist_ok=True)
            self._write_json(run_target / "_meta.json", self._sanitize({k: v for k, v in run.items() if k != "files"}))
            for file_entry in run["files"]:
                source = project_root / file_entry["path"]
                relative_name = Path(file_entry["filename"])
                content = self._read_text_or_json(source)
                if isinstance(content, (dict, list)):
                    self._write_json(run_target / relative_name, self._sanitize(content))
                else:
                    (run_target / relative_name).write_text(self._sanitize_text(content), encoding="utf-8")

    def _read_text_or_json(self, path: Path) -> Any:
        if path.suffix in {".json", ".jsonl"}:
            if path.suffix == ".jsonl":
                lines = [self._sanitize_text(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
                return {"lines": lines}
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return path.read_text(encoding="utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")

    def _zip_dir(self, source_root: Path, bundle_path: Path) -> None:
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source_root.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=path.relative_to(source_root.parent).as_posix())

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if SENSITIVE_KEY_RE.search(str(key)):
                    result[key] = "[REDACTED]"
                else:
                    result[key] = self._sanitize(item)
            return result
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._sanitize(value.model_dump())
        if isinstance(value, str):
            return self._sanitize_text(value)
        return value

    def _sanitize_text(self, text: str) -> str:
        redacted = SECRET_VALUE_RE.sub("[REDACTED]", text)
        redacted = HOME_PATH_RE.sub("/home/[user]", redacted)
        redacted = re.sub(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)([^\s\"']+)", r"\1\2[REDACTED]", redacted)
        return redacted

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
