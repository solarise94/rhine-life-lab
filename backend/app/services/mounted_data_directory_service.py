from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from fastapi import HTTPException

from app.models.graph import Asset
from app.services.project_service import ProjectService
from app.services.utils import resolve_within, sha256_file, utc_now
from app.services.worker_service import WorkerService


def _asset_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv", ".xlsx", ".xls"}:
        return "table"
    if suffix in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
        return "figure"
    if suffix in {".fastq", ".fq", ".bam", ".cram", ".vcf", ".h5ad", ".rds", ".rdata"}:
        return "bio_data"
    if suffix in {".json", ".yaml", ".yml", ".txt", ".md"}:
        return "document"
    return "binary"


class MountedDataDirectoryService:
    def __init__(
        self,
        project_service: ProjectService,
        worker_service: WorkerService | None = None,
    ) -> None:
        self.project_service = project_service
        self.worker_service = worker_service

    def list_entries(
        self,
        project_id: str,
        *,
        path: str = "",
        kind: str = "all",
        cursor: str | None = None,
        show_hidden: bool = False,
    ) -> dict:
        if kind not in {"directory", "all"}:
            raise HTTPException(status_code=400, detail="kind must be 'directory' or 'all'.")
        self._ensure_project_exists(project_id)

        mount = self.project_service.get_project_data_directory(project_id)
        if mount is None:
            return {"project_id": project_id, "path": path, "items": [], "next_cursor": None, "mounted": False}

        data_root = Path(mount.resolved_path)
        if not data_root.exists():
            return {"project_id": project_id, "path": path, "items": [], "next_cursor": None, "mounted": True, "available": False}

        relative = path.strip("/")
        if ".." in relative.split("/"):
            raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

        try:
            target = resolve_within(data_root, relative) if relative else data_root
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Path does not exist: {relative}")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {relative}")

        try:
            entries = sorted(target.iterdir(), key=lambda entry: entry.name)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=f"Cannot read directory: {exc}") from exc

        def _entry_kind(entry: Path) -> str | None:
            if entry.is_symlink():
                try:
                    resolved = entry.resolve()
                    if resolved != data_root and data_root not in resolved.parents:
                        return None
                except (OSError, RuntimeError):
                    return None
            if entry.is_dir(follow_symlinks=True):
                return "directory"
            if entry.is_file(follow_symlinks=True):
                return "file"
            return None

        filtered: list[Path] = []
        for entry in entries:
            if not show_hidden and entry.name.startswith("."):
                continue
            entry_kind = _entry_kind(entry)
            if entry_kind is None:
                continue
            if kind == "directory" and entry_kind != "directory":
                continue
            filtered.append(entry)

        max_entries = 500
        try:
            offset = int(cursor) if cursor else 0
        except ValueError:
            offset = 0

        if offset >= len(filtered):
            return {"project_id": project_id, "path": relative, "items": [], "next_cursor": None, "mounted": True, "available": True}

        page_entries = filtered[offset : offset + max_entries]
        next_cursor = str(offset + max_entries) if offset + max_entries < len(filtered) else None

        items: list[dict] = []
        for entry in page_entries:
            try:
                stat = entry.stat(follow_symlinks=True)
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except (OSError, PermissionError):
                stat = None
                mtime = None

            if entry.is_dir(follow_symlinks=True):
                items.append({"name": entry.name, "kind": "directory", "mtime": mtime})
            elif entry.is_file(follow_symlinks=True):
                items.append({
                    "name": entry.name,
                    "kind": "file",
                    "size_bytes": stat.st_size if stat else None,
                    "mtime": mtime,
                })

        return {"project_id": project_id, "path": relative, "items": items, "next_cursor": next_cursor, "mounted": True, "available": True}

    def register_asset(self, project_id: str, *, path: str) -> dict:
        self._ensure_project_exists(project_id)
        mount = self.project_service.get_project_data_directory(project_id)
        if mount is None:
            raise HTTPException(status_code=404, detail="Project does not have a mounted data directory.")

        data_root = Path(mount.resolved_path)
        if not data_root.exists():
            raise HTTPException(status_code=404, detail="Mounted data directory is not accessible.")

        relative = path.strip("/")
        if ".." in relative.split("/"):
            raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

        try:
            target = resolve_within(data_root, relative) if relative else data_root
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {relative}")
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Path is not a file: {relative}")

        stat = target.stat()
        size = stat.st_size
        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

        hash_limit = self.project_service.settings.data_mount_hash_limit_bytes
        if size <= hash_limit:
            digest = sha256_file(target)
            integrity_kind = "sha256"
        else:
            digest = None
            integrity_kind = "size_mtime"

        asset_id = f"data_mount_{utc_now().replace(':', '').replace('-', '').replace('Z', '')}_{uuid4().hex[:8]}_{target.stem[:32].lower()}"
        asset_path = f"data_mount/{relative}"

        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()

            existing = next(
                (
                    item for item in graph.assets
                    if item.path == asset_path and item.metadata.get("source") == "mounted_data_directory"
                ),
                None,
            )
            if existing:
                return {"asset": existing.model_dump()}

            asset = Asset(
                asset_id=asset_id,
                asset_type=_asset_type_for_path(target),
                title=target.name,
                status="candidate",
                path=asset_path,
                summary=f"Registered from mounted data directory. Size: {size} bytes.",
                metadata={
                    "source": "mounted_data_directory",
                    "mount_relative_path": relative,
                    "registered_size_bytes": size,
                    "registered_mtime": mtime_iso,
                    "integrity_kind": integrity_kind,
                    "sha256": digest,
                    "registered_at": utc_now(),
                },
            )
            graph.assets.append(asset)
            store.save_assets(graph.assets)

        return {"asset": asset.model_dump()}

    def export_asset(
        self,
        project_id: str,
        *,
        asset_id: str,
        destination_path: str,
        overwrite: bool = False,
        actor: str = "user",
    ) -> dict:
        self._ensure_project_exists(project_id)
        if self.worker_service is None:
            raise HTTPException(status_code=500, detail="Worker service is required for export.")
        if self.worker_service.has_active_runs(project_id):
            raise HTTPException(status_code=409, detail=f"Project {project_id} has active runs and cannot export assets.")

        mount = self.project_service.get_project_data_directory(project_id)
        if mount is None:
            raise HTTPException(status_code=404, detail="Project does not have a mounted data directory.")

        data_root = Path(mount.resolved_path)
        if not data_root.exists():
            raise HTTPException(status_code=404, detail="Mounted data directory is not accessible.")

        project_root = self.project_service.project_path(project_id)
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            asset = next((item for item in graph.assets if item.asset_id == asset_id), None)
            if asset is None:
                raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
            if asset.status != "valid":
                raise HTTPException(status_code=409, detail="Asset must be valid before export.")
            if not any(asset.path.startswith(prefix) for prefix in ("results/", "data/")):
                raise HTTPException(
                    status_code=409,
                    detail="Only valid Blueprint-controlled results/ and data/ assets can be exported.",
                )

            source_path = project_root / asset.path
            if not source_path.exists():
                raise HTTPException(status_code=404, detail=f"Asset file not found on disk: {asset.path}")

            relative_dest = destination_path.strip("/")
            if ".." in relative_dest.split("/"):
                raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

            try:
                dest_path = resolve_within(data_root, relative_dest) if relative_dest else data_root
            except ValueError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc

            if dest_path != data_root and data_root not in dest_path.parents:
                raise HTTPException(status_code=403, detail="Destination is outside the mounted data directory.")

            if dest_path.is_dir():
                dest_path = dest_path / source_path.name

            if dest_path.exists() and not overwrite:
                stem = dest_path.stem
                suffix = dest_path.suffix
                parent = dest_path.parent
                counter = 1
                while True:
                    candidate = parent / f"{stem} ({counter}){suffix}"
                    if not candidate.exists():
                        dest_path = candidate
                        break
                    counter += 1
                    if counter > 9999:
                        raise HTTPException(status_code=409, detail="Could not find a unique destination filename.")

            dest_path.parent.mkdir(parents=True, exist_ok=True)

            fd, temp_str = tempfile.mkstemp(dir=dest_path.parent, prefix=".export_")
            os.close(fd)
            temp_path = Path(temp_str)
            temp_consumed = False
            try:
                shutil.copy2(str(source_path), str(temp_path))
                try:
                    os.link(str(temp_path), str(dest_path))
                    temp_consumed = True
                except FileExistsError:
                    if overwrite:
                        os.replace(str(temp_path), str(dest_path))
                        temp_consumed = True
                    else:
                        suffix_rand = uuid4().hex[:6]
                        stem = dest_path.stem
                        ext = dest_path.suffix
                        dest_path = dest_path.parent / f"{stem}_{suffix_rand}{ext}"
                        os.link(str(temp_path), str(dest_path))
                        temp_consumed = True
                if temp_consumed and temp_path.exists():
                    os.unlink(str(temp_path))
            except Exception:
                if not temp_consumed and temp_path.exists():
                    try:
                        os.unlink(str(temp_path))
                    except OSError:
                        pass
                raise

            exported_at = utc_now()
            history = graph.metadata.get("export_history", []) if isinstance(graph.metadata, dict) else []
            history.append(
                {
                    "asset_id": asset_id,
                    "source_path": asset.path,
                    "destination_path": str(dest_path.relative_to(data_root)),
                    "exported_at": exported_at,
                    "actor": actor,
                }
            )
            graph.metadata["export_history"] = history
            store.save_graph(graph)

        return {
            "ok": True,
            "asset_id": asset_id,
            "source_path": asset.path,
            "destination_path": str(dest_path.relative_to(data_root)),
            "exported_at": exported_at,
        }

    def _ensure_project_exists(self, project_id: str) -> None:
        if not (self.project_service.project_path(project_id) / "project.json").exists():
            raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
