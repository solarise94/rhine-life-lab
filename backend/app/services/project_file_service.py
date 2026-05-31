from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi.responses import FileResponse

from app.models.graph import Asset
from app.services.project_service import ProjectService
from app.services.utils import resolve_within


MAX_EXECUTION_FILE_ENTRIES = 200


class ProjectFileService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def list_files(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        assets = snapshot["graph"].assets
        data_assets = [asset for asset in assets if not self._is_session_upload(asset)]
        session_uploads = [asset for asset in assets if self._is_session_upload(asset)]
        active_data_assets, stale_data_assets = self._classify_data_assets(
            data_assets,
            snapshot["cards"],
            snapshot["graph"].modules,
            snapshot["graph"].report_items,
            snapshot["graph"].assets,
        )
        execution_files = self._list_execution_files(project_id)
        return {
            "data_assets": data_assets,
            "active_data_assets": active_data_assets,
            "stale_data_assets": stale_data_assets,
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

    def delete_data_asset(self, project_id: str, asset_id: str) -> Asset:
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            graph = store.load_graph()
            asset = next((item for item in graph.assets if item.asset_id == asset_id), None)
            if asset is None:
                raise FileNotFoundError(asset_id)
            if self._is_session_upload(asset):
                raise PermissionError(f"Asset is a session upload; use the session upload delete endpoint: {asset_id}")

            card_refs = [
                card.card_id
                for card in cards
                if asset_id in card.linked_assets
                or any(item.asset_id == asset_id for item in card.inputs)
                or any(item.asset_id == asset_id for item in card.outputs)
            ]
            if card_refs:
                raise ValueError(f"Asset {asset_id} is still referenced by cards: {', '.join(sorted(card_refs))}")

            downstream_assets = [item.asset_id for item in graph.assets if item.asset_id != asset_id and asset_id in item.depends_on]
            if downstream_assets:
                raise ValueError(f"Asset {asset_id} is still used by downstream assets: {', '.join(sorted(downstream_assets))}")

            project_root = self.project_service.project_path(project_id)
            path = resolve_within(project_root, asset.path)
            same_path_asset_exists = any(item.asset_id != asset_id and item.path == asset.path for item in graph.assets)

            graph.assets = [item for item in graph.assets if item.asset_id != asset_id]
            for module in graph.modules:
                module.depends_on_assets = [item for item in module.depends_on_assets if item != asset_id]
            for claim in graph.claims:
                claim.depends_on_assets = [item for item in claim.depends_on_assets if item != asset_id]
            graph.report_items = [
                item
                for item in graph.report_items
                if asset_id not in item.linked_asset_ids
            ]
            store.save_graph(graph)

            if path.is_file() and not same_path_asset_exists:
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
                    ("report_executor_result.py", "executor_tool"),
                    ("manifest.json", "manifest"),
                    ("executor_completion.json", "executor_completion"),
                    ("executor_failure.json", "executor_failure"),
                    ("terminal_report.json", "terminal_report"),
                    ("executor_result_state.json", "executor_result_state"),
                    ("filesystem_audit.json", "filesystem_audit"),
                    ("manager_brief.json", "manager_brief"),
                    ("dependency_issue.json", "dependency_issue"),
                    ("review_context.json", "review_context"),
                    ("reviewer_trace.json", "reviewer_trace"),
                    ("reviewer_trace.jsonl", "reviewer_trace"),
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
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)[:MAX_EXECUTION_FILE_ENTRIES]

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
    def _classify_data_assets(data_assets: list[Asset], cards: list[object], modules: list[object], report_items: list[object], all_assets: list[Asset]) -> tuple[list[Asset], list[Asset]]:
        active_refs: set[str] = set()
        for card in cards:
            active_refs.update(getattr(card, "linked_assets", []) or [])
            active_refs.update(item.asset_id for item in getattr(card, "inputs", []) or [] if item.asset_id)
            active_refs.update(item.asset_id for item in getattr(card, "outputs", []) or [] if item.asset_id)
        for module in modules:
            active_refs.update(getattr(module, "depends_on_assets", []) or [])
        for item in report_items:
            active_refs.update(getattr(item, "linked_asset_ids", []) or [])

        depended_on = {
            dependency
            for asset in all_assets
            for dependency in (asset.depends_on or [])
        }
        old_statuses = {"stale", "superseded", "rejected", "archived", "missing"}
        active: list[Asset] = []
        stale: list[Asset] = []
        for asset in data_assets:
            if asset.status in old_statuses:
                stale.append(asset)
                continue
            if asset.asset_id in active_refs or asset.asset_id in depended_on or asset.report_selected:
                active.append(asset)
                continue
            if asset.created_by_run:
                stale.append(asset)
            else:
                active.append(asset)
        return active, stale

    @staticmethod
    def _is_allowed_execution_path(relative_path: str) -> bool:
        return relative_path.startswith("runs/") or relative_path.startswith("scripts/generated/")
