from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Literal

from app.api.deps import get_flow_service, get_library_registry_service, get_manager_auto_service, get_project_service
from app.services.flow_service import FlowService
from app.services.library_registry_service import LibraryRegistryService
from app.services.manager_auto_service import ManagerAutoService
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    project_id: str
    name: str
    current_goal: str


class UpdateProjectRuntimePreferencesRequest(BaseModel):
    script_preference: Literal["auto", "prefer_python", "prefer_r", "prefer_mixed"] | None = None
    python_runtime: str | None = None
    r_runtime: str | None = None


@router.get("")
def list_projects(project_service: ProjectService = Depends(get_project_service)) -> dict:
    return {"items": project_service.list_projects()}


@router.post("")
def create_project(request: CreateProjectRequest, project_service: ProjectService = Depends(get_project_service)) -> dict:
    project = project_service.create_project(
        project_id=request.project_id,
        name=request.name,
        current_goal=request.current_goal,
    )
    return {"project": project}


@router.delete("/{project_id}")
def delete_project(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    project_service.delete_project(project_id)
    return {"ok": True}


@router.get("/{project_id}")
def get_project(
    project_id: str,
    project_service: ProjectService = Depends(get_project_service),
    manager_auto_service: ManagerAutoService = Depends(get_manager_auto_service),
) -> dict:
    snapshot = project_service.get_project_snapshot(project_id)
    snapshot["manager_auto"] = manager_auto_service.get_state(project_id).model_dump()
    return snapshot


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
