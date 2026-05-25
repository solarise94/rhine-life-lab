from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_library_registry_service
from app.services.library_registry_service import LibraryRegistryService


router = APIRouter(prefix="/library", tags=["library"])

LibraryKind = Literal["skill", "mcp"]


def _coerce_kind(value: str) -> LibraryKind:
    if value not in {"skill", "mcp"}:
        raise HTTPException(status_code=404, detail="Library kind not found.")
    return value


@router.get("/skills")
def list_skills(service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    return service.list_entries("skill")


@router.get("/mcp")
def list_mcp(service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    return service.list_entries("mcp")


@router.get("/skills/search")
def search_skills(
    q: str = Query(default=""),
    runtime: str | None = Query(default=None),
    tags: list[str] = Query(default_factory=list),
    top_k: int = Query(default=8, ge=1, le=20),
    service: LibraryRegistryService = Depends(get_library_registry_service),
) -> dict:
    return service.search_entries("skill", query=q, runtime=runtime, tags=tags, top_k=top_k)


@router.get("/mcp/search")
def search_mcp(
    q: str = Query(default=""),
    runtime: str | None = Query(default=None),
    tags: list[str] = Query(default_factory=list),
    top_k: int = Query(default=8, ge=1, le=20),
    service: LibraryRegistryService = Depends(get_library_registry_service),
) -> dict:
    return service.search_entries("mcp", query=q, runtime=runtime, tags=tags, top_k=top_k)


@router.get("/skills/{entry_id}")
def get_skill_item(entry_id: str, service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    try:
        return service.get_entry("skill", entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/mcp/{entry_id}")
def get_mcp_item(entry_id: str, service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    try:
        return service.get_entry("mcp", entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/skills/refresh")
def refresh_skills(
    force: bool = Query(default=False),
    service: LibraryRegistryService = Depends(get_library_registry_service),
) -> dict:
    return service.refresh_entries("skill", force=force)


@router.post("/mcp/refresh")
def refresh_mcp(
    force: bool = Query(default=False),
    service: LibraryRegistryService = Depends(get_library_registry_service),
) -> dict:
    return service.refresh_entries("mcp", force=force)


@router.post("/skills/{entry_id}/resummarize")
def resummarize_skill(entry_id: str, service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    try:
        return service.resummarize_entry("skill", entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/mcp/{entry_id}/resummarize")
def resummarize_mcp(entry_id: str, service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    try:
        return service.resummarize_entry("mcp", entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
