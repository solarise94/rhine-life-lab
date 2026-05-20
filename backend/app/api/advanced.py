from fastapi import APIRouter, Depends

from app.api.deps import get_project_service
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects/{project_id}/advanced", tags=["advanced"])


@router.get("/graph")
def get_graph(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    snapshot = project_service.get_project_snapshot(project_id)
    return {"graph": snapshot["graph"], "cards": snapshot["cards"]}


@router.get("/git")
def get_git(project_id: str, project_service: ProjectService = Depends(get_project_service)) -> dict:
    return {"items": project_service.git_service(project_id).log()}
