from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi.responses import FileResponse

from app.models.graph import Asset
from app.services.project_service import ProjectService
from app.services.utils import resolve_within


class ProjectFileService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def list_files(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        assets = snapshot["graph"].assets
        data_assets = [asset for asset in assets if not self._is_session_upload(asset)]
        session_uploads = [asset for asset in assets if self._is_session_upload(asset)]
        execution_files = self._list_execution_files(project_id)
        return {
            "data_assets": data_assets,
            "session_uploads": session_uploads,
            "execution_files": execution_files,
        }

    def delete_session_upload(self, project_id: str, asset_id: str) -> Asset:
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            asset = next((item for item in graph.assets if item.asset_id == asset_id), None)
            if asset is None:
                raise FileNotFoundError(asset_id)
            if not self._is_session_upload(asset):
                raise PermissionError(asset_id)

            project_root = self.project_service.project_path(project_id)
            path = resolve_within(project_root, asset.path)
            graph.assets = [item for item in graph.assets if item.asset_id != asset_id]
            store.save_graph(graph)
            if path.is_file():
                path.unlink()
            return asset

    def get_execution_file_response(self, project_id: str, relative_path: str) -> FileResponse:
        project_root = self.project_service.project_path(project_id)
        path = resolve_within(project_root, relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        normalized = path.relative_to(project_root).as_posix()
        if not self._is_allowed_execution_path(normalized):
            raise PermissionError(relative_path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name)

    def _list_execution_files(self, project_id: str) -> list[dict]:
        project_root = self.project_service.project_path(project_id)
        items: list[dict] = []
        runs_root = project_root / "runs"
        if runs_root.exists():
            for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
                for filename, category in (
                    ("task_packet.json", "task_packet"),
                    ("adapter_contract.json", "adapter_contract"),
                    ("executor_brief.md", "executor_brief"),
                    ("executor_prompt.md", "executor_prompt"),
                    ("manifest.json", "manifest"),
                    ("filesystem_audit.json", "filesystem_audit"),
                    ("manager_brief.json", "manager_brief"),
                    ("review_context.json", "review_context"),
                    ("transcript.md", "transcript"),
                    ("agent_trace.json", "agent_trace"),
                    ("agent_output_timeline.jsonl", "agent_output_timeline"),
                ):
                    path = run_dir / filename
                    if path.exists():
                        entry = self._build_execution_file_entry(project_root, path, category, run_dir.name)
                        if entry:
                            items.append(entry)
        scripts_root = project_root / "scripts" / "generated"
        if scripts_root.exists():
            for path in sorted(item for item in scripts_root.rglob("*") if item.is_file()):
                entry = self._build_execution_file_entry(project_root, path, "generated_script")
                if entry:
                    items.append(entry)
        return items

    @staticmethod
    def _build_execution_file_entry(project_root: Path, path: Path, category: str, run_id: str | None = None) -> dict | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return {
            "path": path.relative_to(project_root).as_posix(),
            "name": path.name,
            "category": category,
            "run_id": run_id,
            "size_bytes": stat.st_size,
            "updated_at": int(stat.st_mtime),
        }

    @staticmethod
    def _is_session_upload(asset: Asset) -> bool:
        source = str(asset.metadata.get("source") or "")
        if source:
            return source == "manager_chat_upload"
        # Fallback for legacy rows written before metadata.source became canonical.
        return asset.path.startswith("data/uploads/")

    @staticmethod
    def _is_allowed_execution_path(relative_path: str) -> bool:
        return relative_path.startswith("runs/") or relative_path.startswith("scripts/generated/")
