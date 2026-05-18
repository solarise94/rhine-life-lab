from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_chat_job_service, get_manager_service, get_patch_apply_service, get_project_service
from app.models.chat import ChatRequest
from app.models.patches import GraphPatch
from app.services.chat_job_service import ChatJobService
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


@router.post("/chat-jobs")
def create_chat_job(
    project_id: str,
    request: ChatRequest,
    manager_service: ManagerService = Depends(get_manager_service),
    chat_job_service: ChatJobService = Depends(get_chat_job_service),
) -> dict:
    job = chat_job_service.submit(project_id, request, manager_service.chat)
    return {"job_id": job.job_id, "status": job.status}


@router.get("/chat-jobs/{job_id}")
def get_chat_job(project_id: str, job_id: str, chat_job_service: ChatJobService = Depends(get_chat_job_service)) -> dict:
    job = chat_job_service.get(job_id)
    if not job or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="Chat job not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "response": job.response.model_dump() if job.response else None,
        "error": job.error,
    }


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
    proposal = manager_service.get_proposal(project_id, proposal_id)
    if proposal.status != "proposed":
        raise HTTPException(status_code=400, detail=f"Proposal status is {proposal.status}, cannot accept")
    patch_payload = project_service.graph_store(project_id).load_patch(proposal.patch_id)
    if not patch_payload:
        raise HTTPException(status_code=404, detail="Patch not found")
    try:
        result = patch_apply_service.apply_patch(project_id, GraphPatch.model_validate(patch_payload))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    proposal = manager_service.mark_proposal_status(project_id, proposal_id, "accepted")
    return {"proposal": proposal, "apply_result": result, "snapshot": project_service.get_project_snapshot(project_id)}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(project_id: str, proposal_id: str, manager_service: ManagerService = Depends(get_manager_service)) -> dict:
    proposal = manager_service.reject_proposal(project_id, proposal_id)
    return {"proposal": proposal}
