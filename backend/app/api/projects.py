from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Literal

import shutil
from pathlib import Path

from app.api.deps import get_flow_service, get_library_registry_service, get_manager_auto_service, get_project_service, get_worker_service
from app.core.config import get_settings
from app.services.flow_service import FlowService
from app.services.library_registry_service import LibraryRegistryService
from app.services.manager_auto_service import ManagerAutoService
from app.services.project_service import ProjectService
from app.services.worker_service import WorkerService

router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    project_id: str
    name: str
    current_goal: str


class UpdateProjectRuntimePreferencesRequest(BaseModel):
    script_preference: Literal["auto", "prefer_python", "prefer_r", "prefer_mixed"] | None = None
    python_runtime: str | None = None
    r_runtime: str | None = None
    execution_mode: Literal["guarded", "workspace_write"] | None = None


class UpdateProjectDataDirectoryRequest(BaseModel):
    root_id: str
    path: str


@router.get("")
def list_projects(project_service: ProjectService = Depends(get_project_service)) -> dict:
    items = project_service.list_projects()
    enriched = []
    for item in items:
        data = item.model_dump()
        if item.data_directory is not None:
            data["data_directory_available"] = Path(item.data_directory.resolved_path).exists()
        else:
            data["data_directory_available"] = None
        enriched.append(data)
    return {"items": enriched}


@router.post("")
def create_project(request: CreateProjectRequest, project_service: ProjectService = Depends(get_project_service)) -> dict:
    project = project_service.create_project(
        project_id=request.project_id,
        name=request.name,
        current_goal=request.current_goal,
    )
    return {"project": project}


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    delete_directory: bool = False,
    project_service: ProjectService = Depends(get_project_service),
    worker_service: WorkerService = Depends(get_worker_service),
) -> dict:
    if worker_service.has_active_runs(project_id):
        raise HTTPException(status_code=409, detail=f"Project {project_id} has active runs and cannot be deleted.")
    project_service.delete_project(project_id, delete_directory=delete_directory)
    return {"ok": True}


@router.get("/{project_id}")
def get_project(
    project_id: str,
    project_service: ProjectService = Depends(get_project_service),
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
) -> dict:
    snapshot = project_service.get_project_snapshot_core(project_id)
    snapshot["manager_auto"] = manager_auto_service.get_state(project_id).model_dump()
    mount = project_service.get_project_data_directory(project_id)
    if mount is not None:
        snapshot["data_directory_available"] = Path(mount.resolved_path).exists()
    else:
        snapshot["data_directory_available"] = None
    return snapshot


@router.get("/{project_id}/environment")
def get_project_environment(
    project_id: str,
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    return project_service.get_project_environment(project_id)


@router.get("/{project_id}/runtime-preferences")
def get_project_runtime_preferences(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    return {"runtime_preferences": project_service.get_project_runtime_preferences(project_id).model_dump()}


@router.put("/{project_id}/runtime-preferences")
def update_project_runtime_preferences(
    project_id: str,
    request: UpdateProjectRuntimePreferencesRequest,
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    return {
        "runtime_preferences": project_service.update_project_runtime_preferences(
            project_id,
            request.model_dump(exclude_unset=True),
        ).model_dump()
    }


@router.put("/{project_id}/data-directory")
def update_project_data_directory(
    project_id: str,
    request: UpdateProjectDataDirectoryRequest,
    project_service: ProjectService = Depends(get_project_service),
    worker_service: WorkerService = Depends(get_worker_service),
) -> dict:
    if worker_service.has_active_runs(project_id):
        raise HTTPException(status_code=409, detail=f"Project {project_id} has active runs and cannot modify data directory.")
    mount = project_service.set_project_data_directory(project_id, request.root_id, request.path)
    return {"data_directory": mount.model_dump()}


@router.get("/{project_id}/data-directory")
def get_project_data_directory(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    mount = project_service.get_project_data_directory(project_id)
    available = None
    if mount is not None:
        available = Path(mount.resolved_path).exists()
    return {"data_directory": mount.model_dump() if mount else None, "available": available}


@router.delete("/{project_id}/data-directory")
def detach_project_data_directory(
    project_id: str,
    project_service: ProjectService = Depends(get_project_service),
    worker_service: WorkerService = Depends(get_worker_service),
) -> dict:
    if worker_service.has_active_runs(project_id):
        raise HTTPException(status_code=409, detail=f"Project {project_id} has active runs and cannot detach data directory.")
    mount = project_service.detach_project_data_directory(project_id)
    return {"data_directory": mount.model_dump() if mount else None, "detached": mount is not None}


@router.get("/{project_id}/data-directory/export-history")
def get_project_data_directory_export_history(
    project_id: str,
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    """Return export history for the project's mounted data directory."""
    if not (project_service.project_path(project_id) / "project.json").exists():
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    store = project_service.graph_store(project_id)
    graph = store.load_graph()
    history = graph.metadata.get("export_history", []) if isinstance(graph.metadata, dict) else []
    history = history or []
    return {"items": history}


@router.get("/{project_id}/skill-library")
def get_skill_library(project_id: str, library_service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    payload = library_service.list_entries("skill")
    payload["project_id"] = project_id
    return payload


@router.get("/{project_id}/mcp-library")
def get_mcp_library(project_id: str, library_service: LibraryRegistryService = Depends(get_library_registry_service)) -> dict:
    payload = library_service.list_entries("mcp")
    payload["project_id"] = project_id
    return payload


class InstallCapabilityRequest(BaseModel):
    kind: str  # "skill" | "mcp"
    source_type: str = "local_path"  # "local_path" | "repo"
    source: str
    overwrite: bool = False


@router.post("/{project_id}/capabilities/install")
def install_capability(
    project_id: str,
    request: InstallCapabilityRequest,
    library_service: LibraryRegistryService = Depends(get_library_registry_service),
) -> dict:
    kind = request.kind.strip()
    if kind not in ("skill", "mcp"):
        raise HTTPException(status_code=422, detail="kind must be 'skill' or 'mcp'")

    if request.source_type not in ("local_path",):
        raise HTTPException(status_code=422, detail="source_type 'repo' is not yet supported; use 'local_path'")

    source = Path(request.source.strip())
    if not source.is_absolute():
        source = Path.cwd() / source
    source = source.resolve()
    if not source.exists():
        raise HTTPException(status_code=422, detail=f"Source path does not exist: {source}")
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="Source must be a directory")

    # Structural validation: verify the source looks like a valid capability
    if kind == "skill" and not (source / "SKILL.md").exists():
        raise HTTPException(status_code=422, detail="Skill source must contain a SKILL.md file")
    if kind == "mcp":
        has_entry = (source / "manifest.json").exists() or (source / "server.json").exists() or (source / "mcp.json").exists()
        if not has_entry:
            raise HTTPException(status_code=422, detail="MCP source must contain at least one of: manifest.json, server.json, mcp.json")

    settings = get_settings()
    cap_root = Path(settings.data_root) / "_system" / "capabilities" / ("skills" if kind == "skill" else "mcp")
    dest = cap_root / source.name

    if dest.exists() and not request.overwrite:
        raise HTTPException(status_code=409, detail=f"Target already exists: {dest}. Set overwrite=true to replace.")

    warnings: list[str] = []

    # Copy source into app-installed capabilities directory
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to copy capability: {exc}") from exc

    # Rebuild registry so the new entry is discovered
    try:
        result = library_service.refresh_entries(kind, force=True)  # type: ignore[arg-type]
        if result.get("refreshed", 0) == 0:
            warnings.append("Registry rebuild completed but no entries were discovered.")
    except Exception as exc:
        warnings.append(f"Post-install registry refresh failed: {exc}")

    # Find the installed entry id
    installed_id = source.name
    installed_name = installed_id

    # Look it up in the refreshed registry for a friendly name
    try:
        for item in (library_service.list_entries(kind).get("items") or []):  # type: ignore[arg-type]
            if isinstance(item, dict) and item.get("id") == installed_id:
                installed_name = item.get("name", installed_id)
                break
    except Exception:
        pass

    return {
        "ok": True,
        "kind": kind,
        "installed_id": installed_id,
        "installed_name": installed_name,
        "summary": f"{'Skill' if kind == 'skill' else 'MCP'} '{installed_name}' installed and available.",
        "warnings": warnings,
    }


@router.get("/{project_id}/cards")
def get_cards(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    snapshot = project_service.get_project_snapshot(project_id)
    return {"items": snapshot["cards"]}


@router.get("/{project_id}/asset-flow")
def get_asset_flow(project_id: str, flow_service: FlowService = Depends(get_flow_service)) -> dict:
    return flow_service.get_asset_flow(project_id)


@router.get("/{project_id}/work-order")
def get_work_order(project_id: str, flow_service: FlowService = Depends(get_flow_service)) -> dict:
    return flow_service.get_work_order(project_id)
