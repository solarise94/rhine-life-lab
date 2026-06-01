from __future__ import annotations

import csv
import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.models.graph import Asset
from app.services.asset_materialization_service import AssetMaterializationService
from app.services.project_service import ProjectService
from app.services.utils import resolve_within


class ResultAssetService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def get_asset(self, project_id: str, asset_id: str) -> Asset:
        graph = self.project_service.graph_store(project_id).load_graph()
        asset = next((item for item in graph.assets if item.asset_id == asset_id), None)
        if asset is None:
            # Resolve logical id through materialization binding
            binding = AssetMaterializationService.current_for_logical(graph, asset_id)
            if binding:
                current_id = binding.get("current_asset_id")
                if current_id:
                    asset = next((item for item in graph.assets if item.asset_id == current_id), None)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Result asset not found: {asset_id}")
        return asset

    def get_asset_detail(self, project_id: str, asset_id: str) -> dict:
        asset = self.get_asset(project_id, asset_id)
        project_root = self.project_service.project_path(project_id)
        path = resolve_within(project_root, asset.path)
        exists = path.exists()
        preview = {
            "kind": "missing" if not exists else "binary",
            "content_type": mimetypes.guess_type(path.name)[0] if exists else None,
            "text": None,
            "table": None,
            "content_url": f"/api/projects/{project_id}/results/{asset_id}/content" if exists else None,
            "size_bytes": path.stat().st_size if exists else None,
        }
        if exists:
            preview = self._build_preview(project_id, asset, path)
        return {
            "asset": asset,
            "preview": preview,
        }

    def get_asset_content_response(self, project_id: str, asset_id: str) -> FileResponse:
        asset = self.get_asset(project_id, asset_id)
        project_root = self.project_service.project_path(project_id)
        path = resolve_within(project_root, asset.path)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name)

    def _build_preview(self, project_id: str, asset: Asset, path: Path) -> dict:
        suffix = path.suffix.lower()
        content_url = f"/api/projects/{project_id}/results/{asset.asset_id}/content"
        if suffix == ".svg":
            return {
                "kind": "image",
                "content_type": mimetypes.guess_type(path.name)[0],
                "text": None,
                "table": None,
                "content_url": content_url,
                "size_bytes": path.stat().st_size,
            }
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            text_fallback = self._read_text(path)
            if text_fallback is None:
                return {
                    "kind": "image",
                    "content_type": mimetypes.guess_type(path.name)[0],
                    "text": None,
                    "table": None,
                    "content_url": content_url,
                    "size_bytes": path.stat().st_size,
                }
            return {
                "kind": "text",
                "content_type": "text/plain",
                "text": text_fallback,
                "table": None,
                "content_url": content_url,
                "size_bytes": path.stat().st_size,
            }
        if suffix in {".csv", ".tsv"}:
            delimiter = "\t" if suffix == ".tsv" else ","
            rows = self._read_table(path, delimiter)
            return {
                "kind": "table",
                "content_type": "text/tab-separated-values" if suffix == ".tsv" else "text/csv",
                "text": None,
                "table": rows,
                "content_url": content_url,
                "size_bytes": path.stat().st_size,
            }
        text = self._read_text(path)
        if text is not None:
            return {
                "kind": "markdown" if suffix == ".md" else "text",
                "content_type": "text/markdown" if suffix == ".md" else "text/plain",
                "text": text,
                "table": None,
                "content_url": content_url,
                "size_bytes": path.stat().st_size,
            }
        return {
            "kind": "binary",
            "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "text": None,
            "table": None,
            "content_url": content_url,
            "size_bytes": path.stat().st_size,
        }

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")[:20000]
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _read_table(path: Path, delimiter: str) -> dict:
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            rows = list(reader)
        if not rows:
            return {"columns": [], "rows": []}
        columns = rows[0]
        values = rows[1:16]
        return {"columns": columns, "rows": values}
