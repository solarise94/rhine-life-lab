from __future__ import annotations

from pydantic import ValidationError

from app.models.runs import Manifest, ManifestReviewContext, TaskPacket
from app.services.project_service import ProjectService
from app.services.utils import atomic_write_json, read_json, resolve_within, sha256_file


class ManifestService:
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
        seen_paths: set[str] = set()
        for asset in manifest.created_assets:
            try:
                path = resolve_within(root, asset.path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if asset.path in seen_paths:
                errors.append(f"Duplicate output path in manifest: {asset.path}")
            seen_paths.add(asset.path)
            if not asset.path.startswith(allowed_prefixes):
                errors.append(f"Manifest output is outside allowed_paths: {asset.path}")
            if not path.exists():
                errors.append(f"Missing output file: {asset.path}")
        return not errors, errors

    def manifest_to_review_context(self, project_id: str, run_id: str) -> ManifestReviewContext:
        manifest = self.load_manifest(project_id, run_id)
        valid, errors = self.validate_manifest(project_id, run_id)
        root = self.project_service.project_path(project_id)
        created_assets = []
        for asset in manifest.created_assets:
            try:
                path = resolve_within(root, asset.path)
            except ValueError:
                continue
            created_assets.append(
                {
                    "role": asset.role,
                    "type": asset.type,
                    "path": asset.path,
                    "description": asset.description,
                    "exists": path.exists(),
                    "sha256": sha256_file(path) if path.exists() else None,
                    "size_bytes": path.stat().st_size if path.exists() else None,
                }
            )
        context = ManifestReviewContext(
            run_id=run_id,
            summary=manifest.summary,
            status=manifest.status,
            created_assets=created_assets,
            commands_executed=manifest.commands_executed,
            metrics=manifest.metrics,
            key_findings=manifest.key_findings,
            warnings=manifest.warnings,
            validation_errors=[] if valid else errors,
        )
        atomic_write_json(root / "runs" / run_id / "review_context.json", context.model_dump())
        return context
