from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import FileResponse

from uuid import uuid4

from app.api.deps import get_project_file_service, get_project_service, get_worker_service
from app.models.graph import Asset
from app.services.project_file_service import ProjectFileService
from app.services.project_service import ProjectService
from app.services.worker_service import WorkerService
import os
import shutil
import tempfile

from app.services.utils import atomic_write_json, resolve_within, sha256_file, utc_now

router = APIRouter(prefix="/projects/{project_id}", tags=["files"])


@router.get("/files")
def get_project_files(project_id: str, project_file_service: ProjectFileService = Depends(get_project_file_service)) -> dict:
    return project_file_service.list_files(project_id)


@router.delete("/files/session-uploads/{asset_id}")
def delete_session_upload(
    project_id: str,
    asset_id: str,
    project_file_service: ProjectFileService = Depends(get_project_file_service),
) -> dict:
    try:
        asset = project_file_service.delete_session_upload(project_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Session upload not found: {asset_id}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Asset is not a session upload: {asset_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "asset": asset}


@router.delete("/files/assets/{asset_id}")
def delete_data_asset(
    project_id: str,
    asset_id: str,
    project_file_service: ProjectFileService = Depends(get_project_file_service),
) -> dict:
    try:
        asset = project_file_service.delete_data_asset(project_id, asset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "asset": asset}


@router.get("/files/content")
def get_project_file_content(
    project_id: str,
    path: str = Query(...),
    project_file_service: ProjectFileService = Depends(get_project_file_service),
) -> FileResponse:
    try:
        return project_file_service.get_execution_file_response(project_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Path is not an execution file: {path}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/work-entries")
def list_project_work_entries(
    project_id: str,
    path: str = Query(default=""),
    kind: Literal["directory", "all"] = Query(default="all"),
    cursor: str | None = Query(default=None),
    show_hidden: bool = Query(default=False),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    """List entries under the project's work/ directory.

    - ``kind=directory``: returns directories only.
    - ``kind=all``: returns directories and files (default for Files panel).
    - ``cursor``: pagination cursor.
    - ``show_hidden``: whether to include dot-entries.
    """
    project_root = project_service.project_path(project_id)
    # Fast-fail if the project itself is missing
    if not (project_root / "project.json").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    work_root = project_root / "work"
    # Legacy projects may not have a work/ directory yet; treat as empty.
    if not work_root.exists():
        return {"project_id": project_id, "path": path, "items": [], "next_cursor": None}

    # Normalize and guard against traversal
    relative = path.strip("/")
    if ".." in relative.split("/"):
        raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

    try:
        target = resolve_within(work_root, relative) if relative else work_root
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path does not exist: {relative}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {relative}")

    try:
        entries = list(target.iterdir())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Cannot read directory: {exc}") from exc

    entries = sorted(entries, key=lambda e: e.name)

    def _entry_kind(entry: Path) -> str | None:
        """Return 'directory', 'file', or None. Resolves symlinks and checks boundary."""
        if entry.is_symlink():
            try:
                resolved = entry.resolve()
                if resolved != work_root and work_root not in resolved.parents:
                    return None
            except (OSError, RuntimeError):
                return None
        if entry.is_dir(follow_symlinks=True):
            return "directory"
        if entry.is_file(follow_symlinks=True):
            return "file"
        return None

    # Filter first, then paginate
    filtered: list[Path] = []
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        ek = _entry_kind(entry)
        if ek is None:
            continue
        if kind == "directory" and ek != "directory":
            continue
        if kind == "all" and ek not in {"directory", "file"}:
            continue
        filtered.append(entry)

    MAX_ENTRIES = 500
    if cursor:
        try:
            offset = int(cursor)
        except ValueError:
            offset = 0
    else:
        offset = 0

    if offset >= len(filtered):
        return {"project_id": project_id, "path": relative, "items": [], "next_cursor": None}

    page_entries = filtered[offset : offset + MAX_ENTRIES]
    next_cursor = str(offset + MAX_ENTRIES) if offset + MAX_ENTRIES < len(filtered) else None

    items: list[dict] = []
    for entry in page_entries:
        name = entry.name
        try:
            stat = entry.stat(follow_symlinks=True)
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OSError, PermissionError):
            mtime_iso = None

        if entry.is_dir(follow_symlinks=True):
            items.append({
                "name": name,
                "kind": "directory",
                "mtime": mtime_iso,
            })
        elif entry.is_file(follow_symlinks=True):
            try:
                size_bytes = entry.stat(follow_symlinks=True).st_size
            except (OSError, PermissionError):
                size_bytes = None
            items.append({
                "name": name,
                "kind": "file",
                "size_bytes": size_bytes,
                "mtime": mtime_iso,
            })

    return {"project_id": project_id, "path": relative, "items": items, "next_cursor": next_cursor}


class RegisterWorkAssetRequest(BaseModel):
    path: str


def _asset_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".html"}:
        return "document"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".bmp", ".webp", ".tiff"}:
        return "image"
    if suffix in {".py", ".r", ".rs", ".js", ".ts", ".c", ".cpp", ".go", ".java", ".sh"}:
        return "script"
    return "data"


@router.post("/work-assets/register")
def register_work_asset(
    project_id: str,
    request: RegisterWorkAssetRequest,
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    """Register a file under the project's work/ directory as a project asset."""
    project_root = project_service.project_path(project_id)
    if not (project_root / "project.json").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    work_root = project_root / "work"
    if not work_root.exists():
        raise HTTPException(status_code=404, detail="Work directory does not exist for this project.")

    relative = request.path.strip("/")
    if ".." in relative.split("/"):
        raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

    try:
        target = resolve_within(work_root, relative) if relative else work_root
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {relative}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {relative}")

    size = target.stat().st_size
    digest = sha256_file(target)
    asset_id = f"work_{utc_now().replace(':', '').replace('-', '').replace('Z', '')}_{uuid4().hex[:8]}_{target.stem[:32].lower()}"
    asset_path = f"work/{relative}"

    with project_service.lock_for(project_id):
        store = project_service.graph_store(project_id)
        graph = store.load_graph()
        if any(existing.asset_id == asset_id for existing in graph.assets):
            raise HTTPException(status_code=409, detail="Asset id collision")

        existing = next(
            (
                item for item in graph.assets
                if item.path == asset_path and item.metadata.get("source") == "work_directory"
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
            summary=f"Registered from work directory. Size: {size} bytes.",
            metadata={
                "source": "work_directory",
                "size_bytes": size,
                "sha256": digest,
                "registered_at": utc_now(),
            },
        )
        graph.assets.append(asset)
        store.save_assets(graph.assets)

    return {"asset": asset.model_dump()}


# ------------------------------------------------------------------
# Data directory (mounted data tree)
# ------------------------------------------------------------------

@router.get("/data-directory/entries")
def list_project_data_directory_entries(
    project_id: str,
    path: str = Query(default=""),
    kind: Literal["directory", "all"] = Query(default="all"),
    cursor: str | None = Query(default=None),
    show_hidden: bool = Query(default=False),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    """List entries under the project's mounted data directory."""
    project_root = project_service.project_path(project_id)
    if not (project_root / "project.json").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    mount = project_service.get_project_data_directory(project_id)
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
        entries = list(target.iterdir())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Cannot read directory: {exc}") from exc

    entries = sorted(entries, key=lambda e: e.name)

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
        ek = _entry_kind(entry)
        if ek is None:
            continue
        if kind == "directory" and ek != "directory":
            continue
        if kind == "all" and ek not in {"directory", "file"}:
            continue
        filtered.append(entry)

    MAX_ENTRIES = 500
    if cursor:
        try:
            offset = int(cursor)
        except ValueError:
            offset = 0
    else:
        offset = 0

    if offset >= len(filtered):
        return {"project_id": project_id, "path": relative, "items": [], "next_cursor": None, "mounted": True, "available": True}

    page_entries = filtered[offset : offset + MAX_ENTRIES]
    next_cursor = str(offset + MAX_ENTRIES) if offset + MAX_ENTRIES < len(filtered) else None

    items: list[dict] = []
    for entry in page_entries:
        name = entry.name
        try:
            stat = entry.stat(follow_symlinks=True)
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OSError, PermissionError):
            mtime_iso = None

        if entry.is_dir(follow_symlinks=True):
            items.append({
                "name": name,
                "kind": "directory",
                "mtime": mtime_iso,
            })
        elif entry.is_file(follow_symlinks=True):
            try:
                size_bytes = entry.stat(follow_symlinks=True).st_size
            except (OSError, PermissionError):
                size_bytes = None
            items.append({
                "name": name,
                "kind": "file",
                "size_bytes": size_bytes,
                "mtime": mtime_iso,
            })

    return {"project_id": project_id, "path": relative, "items": items, "next_cursor": next_cursor, "mounted": True, "available": True}


class RegisterDataDirectoryAssetRequest(BaseModel):
    path: str


@router.post("/data-directory/assets/register")
def register_data_directory_asset(
    project_id: str,
    request: RegisterDataDirectoryAssetRequest,
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    """Register a file under the project's mounted data directory as a project asset."""
    project_root = project_service.project_path(project_id)
    if not (project_root / "project.json").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    mount = project_service.get_project_data_directory(project_id)
    if mount is None:
        raise HTTPException(status_code=404, detail="Project does not have a mounted data directory.")

    data_root = Path(mount.resolved_path)
    if not data_root.exists():
        raise HTTPException(status_code=404, detail="Mounted data directory is not accessible.")

    relative = request.path.strip("/")
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

    size = target.stat().st_size
    mtime = target.stat().st_mtime
    mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    # Hash only if below threshold
    hash_limit = project_service.settings.data_mount_hash_limit_bytes
    if size <= hash_limit:
        digest = sha256_file(target)
        integrity_kind = "sha256"
    else:
        digest = None
        integrity_kind = "size_mtime"

    asset_id = f"data_mount_{utc_now().replace(':', '').replace('-', '').replace('Z', '')}_{uuid4().hex[:8]}_{target.stem[:32].lower()}"
    asset_path = f"data_mount/{relative}"

    with project_service.lock_for(project_id):
        store = project_service.graph_store(project_id)
        graph = store.load_graph()
        if any(existing.asset_id == asset_id for existing in graph.assets):
            raise HTTPException(status_code=409, detail="Asset id collision")

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


class ExportAssetToDataDirectoryRequest(BaseModel):
    destination_path: str
    overwrite: bool = False


@router.post("/assets/{asset_id}/export-to-data-directory")
def export_asset_to_data_directory(
    project_id: str,
    asset_id: str,
    request: ExportAssetToDataDirectoryRequest,
    project_service: ProjectService = Depends(get_project_service),
    worker_service: WorkerService = Depends(get_worker_service),
) -> dict:
    """Export an accepted result asset into the project's mounted data directory."""
    project_root = project_service.project_path(project_id)
    if not (project_root / "project.json").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    if worker_service.has_active_runs(project_id):
        raise HTTPException(status_code=409, detail=f"Project {project_id} has active runs and cannot export assets.")

    mount = project_service.get_project_data_directory(project_id)
    if mount is None:
        raise HTTPException(status_code=404, detail="Project does not have a mounted data directory.")

    data_root = Path(mount.resolved_path)
    if not data_root.exists():
        raise HTTPException(status_code=404, detail="Mounted data directory is not accessible.")

    with project_service.lock_for(project_id):
        store = project_service.graph_store(project_id)
        graph = store.load_graph()
        asset = next((a for a in graph.assets if a.asset_id == asset_id), None)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

        # Source must be a valid Blueprint-controlled result or data asset
        if asset.status != "valid":
            raise HTTPException(status_code=409, detail="Asset must be valid before export.")

        controlled_prefixes = ("results/", "data/")
        if not any(asset.path.startswith(p) for p in controlled_prefixes):
            raise HTTPException(status_code=409, detail="Only valid Blueprint-controlled results/ and data/ assets can be exported.")

        source_path = project_root / asset.path
        if not source_path.exists():
            raise HTTPException(status_code=404, detail=f"Asset file not found on disk: {asset.path}")

        relative_dest = request.destination_path.strip("/")
        if ".." in relative_dest.split("/"):
            raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

        try:
            dest_path = resolve_within(data_root, relative_dest) if relative_dest else data_root
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        # Ensure destination stays inside the mounted data directory
        if dest_path != data_root and data_root not in dest_path.parents:
            raise HTTPException(status_code=403, detail="Destination is outside the mounted data directory.")

        # If destination is a directory, append the source filename
        if dest_path.is_dir():
            dest_path = dest_path / source_path.name

        # Conflict resolution: do not overwrite by default
        if dest_path.exists() and not request.overwrite:
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

        # Atomic temp-file-plus-rename for concurrency safety
        fd, temp_str = tempfile.mkstemp(dir=dest_path.parent, prefix=".export_")
        os.close(fd)
        temp_path = Path(temp_str)
        temp_consumed = False
        try:
            shutil.copy2(str(source_path), str(temp_path))
            # Use hard-link + unlink for atomic create-if-not-exists semantics.
            # If dest_path was created between our check and now, fall back to a
            # random suffix instead of overwriting.
            try:
                os.link(str(temp_path), str(dest_path))
                temp_consumed = True
            except FileExistsError:
                if request.overwrite:
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
            # Clean up temp on any unexpected error
            if not temp_consumed and temp_path.exists():
                try:
                    os.unlink(str(temp_path))
                except OSError:
                    pass
            raise

    # Record export audit entry
    with project_service.lock_for(project_id):
        store = project_service.graph_store(project_id)
        graph = store.load_graph()
        export_entry = {
            "asset_id": asset_id,
            "source_path": asset.path,
            "destination_path": str(dest_path.relative_to(data_root)),
            "exported_at": utc_now(),
            "actor": "user",
        }
        history = graph.metadata.get("export_history", [])
        history.append(export_entry)
        graph.metadata["export_history"] = history
        store.save_graph(graph)

    return {
        "ok": True,
        "asset_id": asset_id,
        "source_path": asset.path,
        "destination_path": str(dest_path.relative_to(data_root)),
        "exported_at": utc_now(),
    }
