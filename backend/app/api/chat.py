from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_manager_service, get_patch_apply_service, get_project_service
from app.models.chat import ChatRequest
from app.models.patches import GraphPatch
from app.services.manager_planner import ManagerPlanningError
from app.services.manager_service import ManagerService
from app.services.patch_apply import PatchApplyService
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects/{project_id}", tags=["chat"])


@router.post("/chat")
def chat(project_id: str, request: ChatRequest, manager_service: ManagerService = Depends(get_manager_service)) -> dict:
    try:
        return manager_service.chat(project_id, request).model_dump()
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/proposals/{proposal_id}/modify")
def modify_proposal(
    project_id: str,
    proposal_id: str,
    request: ChatRequest,
    manager_service: ManagerService = Depends(get_manager_service),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        proposal = manager_service.modify_proposal(project_id, proposal_id, request)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    patch_payload = project_service.graph_store(project_id).load_patch(proposal.patch_id)
    if not patch_payload:
        raise HTTPException(status_code=404, detail="Modified patch not found")
    return {"proposal": proposal, "patch": patch_payload}


@router.post("/proposals/{proposal_id}/accept")
def accept_proposal(
    project_id: str,
    proposal_id: str,
    manager_service: ManagerService = Depends(get_manager_service),
    patch_apply_service: PatchApplyService = Depends(get_patch_apply_service),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    proposal = manager_service.accept_proposal(project_id, proposal_id)
    patch_payload = project_service.graph_store(project_id).load_patch(proposal.patch_id)
    if not patch_payload:
        raise HTTPException(status_code=404, detail="Patch not found")
    result = patch_apply_service.apply_patch(project_id, GraphPatch.model_validate(patch_payload))
    return {"proposal": proposal, "apply_result": result, "snapshot": project_service.get_project_snapshot(project_id)}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(project_id: str, proposal_id: str, manager_service: ManagerService = Depends(get_manager_service)) -> dict:
    proposal = manager_service.reject_proposal(project_id, proposal_id)
    return {"proposal": proposal}
