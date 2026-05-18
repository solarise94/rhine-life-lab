from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_project_service
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    project_id: str
    name: str
    current_goal: str


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


@router.get("/{project_id}")
def get_project(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    return project_service.get_project_snapshot(project_id)


@router.get("/{project_id}/cards")
def get_cards(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    snapshot = project_service.get_project_snapshot(project_id)
    return {"items": snapshot["cards"]}

