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


@router.get("/data-assets")
def list_data_assets(
    project_id: str,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.list_data_assets(project_id)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cards")
def create_card(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.create_card(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/cards/{card_id}")
def update_card(
    project_id: str,
    card_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        body = dict(payload)
        body["card_id"] = card_id
        return manager_service.blueprint_tools.update_card(project_id, body)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/card-execution")
def configure_card_execution(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.configure_card_execution(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/cards/{card_id}")
def delete_card(
    project_id: str,
    card_id: str,
    payload: dict | None = None,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        body = dict(payload or {})
        body["card_id"] = card_id
        return manager_service.blueprint_tools.delete_card(project_id, body)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tool-policy")
def get_tool_policy(
    project_id: str,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.get_tool_policy(project_id)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/tool-policy")
def set_tool_policy(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.set_tool_policy(project_id, payload)
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


@router.post("/memory/list")
def list_project_memory(
    project_id: str,
    payload: dict | None = None,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.list_project_memory(project_id, payload or {})
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/memory")
def write_project_memory(
    project_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    manager_service: ManagerService = Depends(get_manager_service),
) -> dict:
    _verify_internal_token(authorization)
    try:
        return manager_service.blueprint_tools.write_project_memory(project_id, payload)
    except ManagerPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
