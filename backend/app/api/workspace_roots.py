from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_project_service
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspace-roots", tags=["workspace-roots"])


@router.get("")
def list_workspace_roots(project_service: ProjectService = Depends(get_project_service)) -> dict:
    return {"items": project_service.data_directory_roots()}


@router.get("/{root_id}/entries")
def list_workspace_entries(
    root_id: str,
    path: str = Query(default=""),
    kind: Literal["directory", "all"] = Query(default="directory"),
    cursor: str | None = Query(default=None),
    show_hidden: bool = Query(default=False),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    """List directory entries under a workspace root.

    - ``kind=directory``: returns directories only (default for project creation browsing).
    - ``kind=all``: returns directories and files (for Files panel browsing).
    - ``cursor``: pagination cursor (not yet implemented; placeholder for large directories).
    - ``show_hidden``: whether to include dot-entries.
    """
    roots = project_service.data_directory_roots()
    root_info = next((r for r in roots if r["root_id"] == root_id), None)
    if root_info is None:
        raise HTTPException(status_code=404, detail=f"Workspace root not found: {root_id}")

    root_path = Path(root_info["path"]).resolve()

    # Normalize the relative path: strip leading/trailing slashes, reject traversal
    relative = path.strip("/")
    if ".." in relative.split("/"):
        raise HTTPException(status_code=403, detail="Path traversal is not allowed.")

    target = (root_path / relative).resolve() if relative else root_path

    # Boundary check: target must be inside root_path (component-aware, not string prefix)
    if target != root_path and root_path not in target.parents:
        raise HTTPException(status_code=403, detail="Requested path is outside the allowed workspace root.")

    # Symlink escape check: resolve symlinks and re-check
    if target.is_symlink():
        real_target = target.resolve()
        if real_target != root_path and root_path not in real_target.parents:
            raise HTTPException(status_code=403, detail="Symlink points outside the allowed workspace root.")

    try:
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Path does not exist: {relative}")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {relative}")
        entries = list(target.iterdir())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Cannot access path: {exc}") from exc

    # Sort by name for stable ordering
    entries = sorted(entries, key=lambda e: e.name)

    def _entry_kind(entry: Path) -> str | None:
        """Return 'directory', 'file', or None. Resolves symlinks and checks boundary."""
        if entry.is_symlink():
            try:
                resolved = entry.resolve()
                # Symlink target must stay inside the root
                if resolved != root_path and root_path not in resolved.parents:
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

    # Pagination
    MAX_ENTRIES = 500
    if cursor:
        try:
            offset = int(cursor)
        except ValueError:
            offset = 0
    else:
        offset = 0

    if offset >= len(filtered):
        return {
            "root_id": root_id,
            "path": relative,
            "items": [],
            "next_cursor": None,
        }

    page_entries = filtered[offset : offset + MAX_ENTRIES]
    next_cursor = str(offset + MAX_ENTRIES) if offset + MAX_ENTRIES < len(filtered) else None

    items: list[dict] = []
    for entry in page_entries:
        name = entry.name
        try:
            stat = entry.stat(follow_symlinks=True)
            mtime = stat.st_mtime
            from datetime import datetime, timezone
            mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OSError, PermissionError):
            mtime_iso = None

        if entry.is_dir(follow_symlinks=True):
            is_empty = False
            try:
                is_empty = not any(entry.iterdir())
            except PermissionError:
                is_empty = False
            items.append({
                "name": name,
                "kind": "directory",
                "is_empty": is_empty,
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

    return {
        "root_id": root_id,
        "path": relative,
        "items": items,
        "next_cursor": next_cursor,
    }
