from __future__ import annotations

from pydantic import ValidationError

from app.models.runs import ExecutorValidationReport, Manifest, ManifestReviewContext, TaskPacket
from app.services.artifact_format_service import detect_artifact_class, detect_artifact_format
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, resolve_within, sha256_file


class ManifestService:
    BACKEND_MANAGED_AUDIT_PATHS = {
        "chat/sessions.json",
        "graph/assets.json",
        "graph/cards.json",
        "graph/claims.json",
        "graph/graph.json",
        "graph/modules.json",
        "graph/report.json",
        "graph/runs.json",
    }

    BACKEND_MANAGED_AUDIT_PREFIXES = (
        "runs/",
    )

    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def load_manifest(self, project_id: str, run_id: str) -> Manifest:
        root = self.project_service.project_path(project_id)
        payload = read_json(root / "runs" / run_id / "manifest.json", {})
        return Manifest.model_validate(payload)

    def load_task_packet(self, project_id: str, run_id: str) -> TaskPacket:
        root = self.project_service.project_path(project_id)
        payload = read_json(root / "runs" / run_id / "task_packet.json", {})
        return TaskPacket.model_validate(payload)

    def validate_manifest(self, project_id: str, run_id: str) -> tuple[bool, list[str]]:
        root = self.project_service.project_path(project_id)
        errors: list[str] = []
        try:
            manifest = self.load_manifest(project_id, run_id)
        except ValidationError as exc:
            return False, [f"Manifest schema validation failed: {exc}"]
        packet = self.load_task_packet(project_id, run_id)
        allowed_prefixes = tuple(packet.allowed_paths)
        expected_outputs = {item.role: item for item in packet.expected_outputs}
        required_output_roles = {item.role for item in packet.expected_outputs if item.required}

        graph = self.project_service.graph_store(project_id).load_graph()
        existing_valid_output_paths = {
            asset.path: asset.asset_id
            for asset in graph.assets
            if asset.status == "valid" and asset.created_by_run != run_id
        }
        seen_paths: set[str] = set()
        seen_roles: set[str] = set()
        for asset in manifest.created_assets:
            try:
                path = resolve_within(root, asset.path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            expected = expected_outputs.get(asset.role)
            if expected is None:
                errors.append(f"Manifest output role is not declared in task packet: {asset.role}")
            else:
                seen_roles.add(asset.role)
            if asset.path in seen_paths:
                errors.append(f"Duplicate output path in manifest: {asset.path}")
            seen_paths.add(asset.path)
            if not asset.path.startswith(allowed_prefixes):
                errors.append(f"Manifest output is outside allowed_paths: {asset.path}")
            if asset.path in existing_valid_output_paths:
                errors.append(
                        f"Manifest output path collides with an existing valid asset: {asset.path} ({existing_valid_output_paths[asset.path]})"
                    )
            if not path.exists():
                errors.append(f"Missing output file: {asset.path}")
                continue
            detected_format = detect_artifact_format(path)
            detected_class = detect_artifact_class(path)
            if expected is not None and detected_class != expected.artifact_class:
                errors.append(
                    f"Manifest output class mismatch for role {asset.role}: expected {expected.artifact_class}, got {detected_class or 'unknown'}"
                )
            if expected is not None and expected.accepted_formats and detected_format not in expected.accepted_formats:
                errors.append(
                    f"Manifest output format mismatch for role {asset.role}: expected one of {', '.join(expected.accepted_formats)}, got {detected_format or 'unknown'}"
                )
        missing_output_roles = sorted(required_output_roles - seen_roles)
        if missing_output_roles:
            errors.append(f"Manifest is missing declared outputs: {', '.join(missing_output_roles)}")
        return not errors, errors

    def capture_filesystem_snapshot(self, project_id: str) -> dict[str, dict[str, int]]:
        root = self.project_service.project_path(project_id)
        snapshot: dict[str, dict[str, int]] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            snapshot[relative] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        return snapshot

    def audit_run_filesystem(
        self,
        project_id: str,
        run_id: str,
        before_snapshot: dict[str, dict[str, int]],
        *,
        sandboxed: bool = False,
    ) -> tuple[bool, list[str], list[dict[str, str]]]:
        packet = self.load_task_packet(project_id, run_id)
        after_snapshot = self.capture_filesystem_snapshot(project_id)
        allowed_prefixes = tuple(packet.allowed_paths)
        changes: list[dict[str, str]] = []
        violations: list[str] = []

        for relative in sorted(set(before_snapshot) | set(after_snapshot)):
            before = before_snapshot.get(relative)
            after = after_snapshot.get(relative)
            if before == after:
                continue
            if before is None:
                change_type = "created"
            elif after is None:
                change_type = "deleted"
            else:
                change_type = "modified"
            changes.append({"path": relative, "change": change_type})
            if relative in self.BACKEND_MANAGED_AUDIT_PATHS:
                continue
            if self._is_backend_managed_run_file(relative, run_id):
                continue
            if sandboxed:
                continue
            if not relative.startswith(allowed_prefixes):
                violations.append(f"Worker {change_type} path outside allowed_paths: {relative}")

        atomic_write_json(
            self.project_service.project_path(project_id) / "runs" / run_id / "filesystem_audit.json",
            {"changes": changes, "violations": violations},
        )
        return not violations, violations, changes

    @staticmethod
    def _is_backend_managed_run_file(relative: str, run_id: str) -> bool:
        return relative in {
            f"runs/{run_id}/.Rprofile",
            f"runs/{run_id}/adapter_contract.json",
            f"runs/{run_id}/commands.log",
            f"runs/{run_id}/events.json",
            f"runs/{run_id}/executor_brief.md",
            f"runs/{run_id}/executor_prompt.md",
            f"runs/{run_id}/filesystem_audit.json",
            f"runs/{run_id}/manager_brief.json",
            f"runs/{run_id}/runtime_approvals.json",
            f"runs/{run_id}/task_packet.json",
            f"runs/{run_id}/transcript.md",
            f"runs/{run_id}/review_context.json",
            f"runs/{run_id}/executor_validation.json",
        }

    def manifest_to_review_context(self, project_id: str, run_id: str) -> ManifestReviewContext:
        manifest = self.load_manifest(project_id, run_id)
        valid, errors = self.validate_manifest(project_id, run_id)
        task_packet = self.load_task_packet(project_id, run_id)
        root = self.project_service.project_path(project_id)
        validation_errors = list(errors)
        validation_evidence = manifest.validation_evidence if isinstance(manifest.validation_evidence, dict) else {}
        input_conclusion = validation_evidence.get("input_conclusion")
        if isinstance(input_conclusion, dict):
            input_conclusion = input_conclusion.get("summary") or input_conclusion.get("text") or str(input_conclusion)
        elif not isinstance(input_conclusion, str):
            input_conclusion = None
        created_assets = []
        for asset in manifest.created_assets:
            try:
                path = resolve_within(root, asset.path)
            except ValueError as exc:
                validation_errors.append(f"Invalid created asset path in review context: {asset.path}: {exc}")
                continue
            exists = path.exists()
            is_file = path.is_file()
            created_assets.append(
                {
                    "role": asset.role,
                    "path": asset.path,
                    "description": asset.description,
                    "exists": exists,
                    "artifact_class": detect_artifact_class(path) if exists and is_file else None,
                    "format": detect_artifact_format(path) if exists and is_file else None,
                    "sha256": sha256_file(path) if exists and is_file else None,
                    "size_bytes": path.stat().st_size if exists and is_file else None,
                    "is_file": is_file,
                }
            )
        code_artifacts = []
        for artifact in manifest.code_artifacts:
            try:
                path = resolve_within(root, artifact.path)
            except ValueError as exc:
                validation_errors.append(f"Invalid code artifact path in review context: {artifact.path}: {exc}")
                continue
            exists = path.exists()
            is_file = path.is_file()
            code_artifacts.append(
                {
                    "path": artifact.path,
                    "language": artifact.language,
                    "purpose": artifact.purpose,
                    "exists": exists,
                    "sha256": sha256_file(path) if exists and is_file else None,
                    "is_file": is_file,
                }
            )
        validation_payload = read_json(root / "runs" / run_id / "executor_validation.json", None)
        executor_validation = None
        if isinstance(validation_payload, dict):
            try:
                executor_validation = ExecutorValidationReport.model_validate(validation_payload)
            except ValidationError:
                executor_validation = None
        context = ManifestReviewContext(
            run_id=run_id,
            summary=manifest.summary,
            status=manifest.status,
            declared_input_assets=[item.model_dump() for item in task_packet.input_assets],
            input_conclusion=input_conclusion,
            created_assets=created_assets,
            code_artifacts=code_artifacts,
            commands_executed=manifest.commands_executed,
            metrics=manifest.metrics,
            key_findings=manifest.key_findings,
            warnings=manifest.warnings,
            validation_errors=[] if valid and not validation_errors else validation_errors,
            executor_validation=executor_validation,
        )
        atomic_write_json(root / "runs" / run_id / "review_context.json", context.model_dump())
        return context
