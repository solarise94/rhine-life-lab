from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.models.runs import ExecutorValidationReport, Manifest, TaskPacket, ValidationIssue
from app.services.executor_reviewer_worker import ExecutorReviewerWorker
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, resolve_within, sha256_file


logger = logging.getLogger(__name__)


class ExecutorValidationService:
    def __init__(self, project_service: ProjectService, settings: Settings | None = None) -> None:
        self.project_service = project_service
        self.settings = settings or project_service.settings
        self.reviewer_worker = ExecutorReviewerWorker(self.settings)

    def validate_run(self, project_id: str, run_id: str) -> ExecutorValidationReport:
        root = self.project_service.project_path(project_id)
        packet = self._load_task_packet(root, run_id)
        manifest = self._load_manifest(root, run_id)
        issues = self._deterministic_issues(root, packet, manifest)
        deterministic_status = "fail" if any(issue.severity == "error" for issue in issues) else "warn" if issues else "pass"

        try:
            reviewer_payload = self.reviewer_worker.review(
                root=root,
                packet=packet,
                manifest=manifest,
                deterministic_issues=issues,
            )
        except Exception as exc:
            logger.exception("Executor reviewer infrastructure failed for project=%s run=%s", project_id, run_id)
            reviewer_payload = {
                "verdict": "warn",
                "summary": f"Executor reviewer failed before producing a verdict: {exc}",
                "issues": [
                    {
                        "severity": "warning",
                        "code": "reviewer_infrastructure_error",
                        "message": str(exc),
                        "repair_hint": "Check Manager AI DeepSeek configuration and reviewer tool-use compatibility, then rerun validation.",
                    }
                ],
                "mode": "reviewer_infrastructure_error",
            }
        reviewer_status = str(reviewer_payload.get("verdict") or "warn")
        if reviewer_status not in {"pass", "warn", "fail"}:
            reviewer_status = "warn"
        for issue in reviewer_payload.get("issues", []):
            if isinstance(issue, dict):
                severity = str(issue.get("severity") or ("error" if reviewer_status == "fail" else "warning"))
                if severity not in {"info", "warning", "error"}:
                    severity = "warning"
                issues.append(
                    ValidationIssue(
                        severity=severity,  # type: ignore[arg-type]
                        code=str(issue.get("code") or "reviewer_issue"),
                        message=str(issue.get("message") or issue.get("summary") or issue),
                        path=issue.get("path") if isinstance(issue.get("path"), str) else None,
                        repair_hint=issue.get("repair_hint") if isinstance(issue.get("repair_hint"), str) else None,
                    )
                )

        manager_brief_parse_issue = self._manager_brief_parse_issue(root / "runs" / run_id / "manager_brief.json")
        if manager_brief_parse_issue is not None:
            issues.append(manager_brief_parse_issue)

        issue_status = "fail" if any(issue.severity == "error" for issue in issues) else "warn" if issues else deterministic_status
        status = self._combine_status(issue_status, reviewer_status)
        report = ExecutorValidationReport(
            status=status,
            summary=self._summary_for(status, issues, reviewer_payload),
            issues=issues,
            reviewer=reviewer_payload,
        )
        atomic_write_json(root / "runs" / run_id / "executor_validation.json", report.model_dump())
        self._merge_manager_brief(root / "runs" / run_id / "manager_brief.json", report)
        return report

    @staticmethod
    def _load_task_packet(root: Path, run_id: str) -> TaskPacket:
        return TaskPacket.model_validate(json.loads((root / "runs" / run_id / "task_packet.json").read_text(encoding="utf-8")))

    @staticmethod
    def _load_manifest(root: Path, run_id: str) -> Manifest:
        return Manifest.model_validate(json.loads((root / "runs" / run_id / "manifest.json").read_text(encoding="utf-8")))

    def _deterministic_issues(self, root: Path, packet: TaskPacket, manifest: Manifest) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if manifest.created_assets and not manifest.code_artifacts:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="missing_code_artifact",
                    message="Manifest created assets but did not declare preserved executor code.",
                    repair_hint=(
                        f"Preserve executed code under scripts/generated/{packet.task_id}/ "
                        "and declare it in manifest.code_artifacts."
                    ),
                )
            )

        for artifact in manifest.code_artifacts:
            if not artifact.path.startswith((f"scripts/generated/{packet.task_id}/", f"runs/{packet.task_id}/")):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="code_artifact_outside_allowed_scope",
                        message="Code artifact must be under the run script or run directory scope.",
                        path=artifact.path,
                    )
                )
                continue
            try:
                path = resolve_within(root, artifact.path)
            except ValueError as exc:
                issues.append(ValidationIssue(severity="error", code="invalid_code_artifact_path", message=str(exc), path=artifact.path))
                continue
            if not path.exists():
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="missing_code_artifact_file",
                        message="Declared code artifact file does not exist.",
                        path=artifact.path,
                    )
                )
                continue
            if not path.is_file():
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="code_artifact_not_file",
                        message="Declared code artifact path is not a file.",
                        path=artifact.path,
                        repair_hint="Declare the executed script or notebook file, not only its containing directory.",
                    )
                )
                continue
            if artifact.sha256 and sha256_file(path) != artifact.sha256:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="code_artifact_hash_mismatch",
                        message="Declared code artifact sha256 does not match the file.",
                        path=artifact.path,
                    )
                )

        for asset in manifest.created_assets:
            try:
                path = resolve_within(root, asset.path)
            except ValueError as exc:
                issues.append(ValidationIssue(severity="error", code="invalid_output_path", message=str(exc), path=asset.path))
                continue
            if not path.exists():
                continue
            size = path.stat().st_size
            if size == 0:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="empty_output",
                        message="Created output file is empty.",
                        path=asset.path,
                    )
                )
            if asset.type == "table" and self._looks_like_placeholder_table(path):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="placeholder_table_detected",
                        message="Table output looks like a scaffold/demo placeholder rather than real analysis data.",
                        path=asset.path,
                        repair_hint="Regenerate this output from the declared input assets using preserved analysis code.",
                    )
                )

        manager_brief = root / "runs" / packet.task_id / "manager_brief.json"
        if not manager_brief.exists():
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="missing_manager_brief",
                    message="Executor did not write manager_brief.json; backend will rely on manifest and captured BP_EVENTs.",
                    repair_hint="Write a concise manager_brief.json for Manager review.",
                )
            )
        return issues

    @staticmethod
    def _looks_like_placeholder_table(path: Path) -> bool:
        text = path.read_text(encoding="utf-8", errors="replace")[:2048].lower()
        compact = " ".join(text.split())
        return (
            "term_1" in compact
            and "term_2" in compact
            and ("feature score" in compact or "synthetic output" in compact)
        )

    @staticmethod
    def _combine_status(deterministic_status: str, reviewer_status: str) -> str:
        if "fail" in {deterministic_status, reviewer_status}:
            return "fail"
        if "warn" in {deterministic_status, reviewer_status}:
            return "warn"
        return "pass"

    @staticmethod
    def _summary_for(status: str, issues: list[ValidationIssue], reviewer_payload: dict[str, Any]) -> str:
        if status == "pass":
            return reviewer_payload.get("summary") or "Executor outputs passed validation."
        if status == "warn":
            return reviewer_payload.get("summary") or f"Executor outputs passed with {len(issues)} warning(s)."
        return reviewer_payload.get("summary") or f"Executor validation failed with {len(issues)} issue(s)."

    @staticmethod
    def _manager_brief_parse_issue(path: Path) -> ValidationIssue | None:
        if not path.exists():
            return None
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return ValidationIssue(
                severity="warning",
                code="manager_brief_json_invalid",
                message=f"manager_brief.json could not be parsed: {exc}",
                path=str(path.name),
                repair_hint="Rewrite manager_brief.json as valid JSON so the Manager can read the executor final report.",
            )
        return None

    @staticmethod
    def _merge_manager_brief(path: Path, report: ExecutorValidationReport) -> None:
        brief: dict[str, Any] = {}
        if path.exists():
            try:
                brief = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.exception("Failed to parse manager_brief before merging executor validation: %s", path)
                brief = {"manager_brief_parse_error": str(exc)}
        brief["executor_validation"] = report.model_dump()
        atomic_write_json(path, brief)
