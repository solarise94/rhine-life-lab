from pathlib import Path
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_job_service, get_manager_service, get_patch_apply_service, get_project_service
from app.models.chat import ChatRequest
from app.models.graph import Asset
from app.models.patches import GraphPatch
from app.services.chat_job_service import ChatJobService
from app.services.manager_planner import ManagerPlanningError
from app.services.manager_service import ManagerService
from app.services.patch_apply import PatchApplyService
from app.services.project_service import ProjectService
from app.services.utils import sha256_file, utc_now

router = APIRouter(prefix="/projects/{project_id}", tags=["chat"])

MAX_CHAT_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/chat")
def chat(project_id: str, request: ChatRequest, manager_service: ManagerService = Depends(get_manager_service)) -> dict:
    try:
        return manager_service.chat(project_id, request).model_dump()
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/chat-stream")
def chat_stream(project_id: str, request: ChatRequest, manager_service: ManagerService = Depends(get_manager_service)) -> StreamingResponse:
    try:
        stream = manager_service.stream_chat(project_id, request)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


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


@router.post("/chat-uploads")
async def upload_chat_file(
    project_id: str,
    file: UploadFile = File(...),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    safe_name = _safe_filename(file.filename)
    timestamp = utc_now().replace(":", "").replace("-", "").replace("Z", "")
    asset_id = f"upload_{timestamp}_{Path(safe_name).stem[:32].lower()}"
    relative_path = f"data/uploads/{asset_id}_{safe_name}"
    project_root = project_service.project_path(project_id)
    target = project_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CHAT_UPLOAD_BYTES:
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="File is larger than 50MB")
                handle.write(chunk)
    finally:
        await file.close()

    store = project_service.graph_store(project_id)
    graph = store.load_graph()
    if any(asset.asset_id == asset_id for asset in graph.assets):
        raise HTTPException(status_code=409, detail="Uploaded asset id collision")

    asset = Asset(
        asset_id=asset_id,
        asset_type=_asset_type_for_upload(file.content_type, safe_name),
        title=file.filename,
        status="candidate",
        path=relative_path,
        summary=f"User uploaded file for Manager AI chat. Size: {size} bytes.",
        metadata={
            "source": "manager_chat_upload",
            "content_type": file.content_type,
            "size_bytes": size,
            "sha256": sha256_file(target),
            "uploaded_at": utc_now(),
        },
    )
    graph.assets.append(asset)
    store.save_graph(graph)

    return {
        "asset": asset,
        "attachment": {
            "type": "asset",
            "id": asset.asset_id,
            "label": asset.title,
        },
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


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name[:120] or "upload.bin"


def _asset_type_for_upload(content_type: str | None, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if content_type and content_type.startswith("image/"):
        return "figure"
    if suffix in {".tsv", ".csv", ".xlsx", ".xls"}:
        return "table"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {".txt", ".log", ".json", ".yaml", ".yml"}:
        return "text"
    return "uploaded_file"


@router.post("/proposals/{proposal_id}/accept")
def accept_proposal(
    project_id: str,
    proposal_id: str,
    manager_service: ManagerService = Depends(get_manager_service),
    patch_apply_service: PatchApplyService = Depends(get_patch_apply_service),
    project_service: ProjectService = Depends(get_project_service),
) -> dict:
    try:
        proposal = manager_service.get_proposal(project_id, proposal_id)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    try:
        proposal = manager_service.mark_proposal_status(project_id, proposal_id, "accepted")
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"proposal": proposal, "apply_result": result, "snapshot": project_service.get_project_snapshot(project_id)}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(project_id: str, proposal_id: str, manager_service: ManagerService = Depends(get_manager_service)) -> dict:
    try:
        proposal = manager_service.reject_proposal(project_id, proposal_id)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"proposal": proposal}
