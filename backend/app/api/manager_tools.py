from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.deps import get_manager_service
from app.core.config import get_settings
from app.services.manager_planner import ManagerPlanningError
from app.services.manager_service import ManagerService

router = APIRouter(prefix="/internal/manager-tools/projects/{project_id}", tags=["manager-tools"])


def _verify_internal_token(authorization: str | None) -> None:
    settings = get_settings()
    expected = settings.internal_tool_token.get_secret_value() if settings.internal_tool_token else ""
    if not expected:
        raise HTTPException(status_code=503, detail="BLUEPRINT_INTERNAL_TOOL_TOKEN is not configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid internal tool token")


@router.get("/context")
def get_project_context(
    project_id: str,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    return manager_service.blueprint_tools.get_project_context(project_id)


@router.post("/plan-blueprint")
def plan_blueprint(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.plan_blueprint(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/review-blueprint-plan")
def review_blueprint_plan(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.review_blueprint_plan(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/proposals")
def save_patch_proposal(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        response = manager_service.blueprint_tools.save_patch_proposal(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response.model_dump()


@router.post("/delete-module")
def delete_module_proposal(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        response = manager_service.blueprint_tools.delete_module_proposal(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response.model_dump()


@router.post("/delete-card")
def delete_card_proposal(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        response = manager_service.blueprint_tools.delete_card_proposal(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response.model_dump()


@router.post("/restore-module")
def restore_module_proposal(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        response = manager_service.blueprint_tools.restore_module_proposal(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response.model_dump()


@router.post("/restore-card")
def restore_card_proposal(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        response = manager_service.blueprint_tools.restore_card_proposal(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response.model_dump()


@router.post("/proposals/{proposal_id}/modify")
def modify_proposal(
    project_id: str,
    proposal_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.modify_proposal(project_id, proposal_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/assets/{asset_id}")
def read_result_asset(
    project_id: str,
    asset_id: str,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.read_result_asset(project_id, asset_id)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
