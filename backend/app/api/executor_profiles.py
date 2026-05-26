from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_app_config_service
from app.models.executor_profiles import (
    SUPPORTED_AUTH_MODES,
    SUPPORTED_API_PROTOCOLS,
    ExecutorProfileSpec,
    default_profiles,
    validate_profile,
)
from app.services.app_config_service import AppConfigService


router = APIRouter(prefix="/executor-profiles", tags=["executor-profiles"])


class ExecutorProfileRequest(BaseModel):
    profile_id: str
    display_name: str
    worker_type: str
    auth_mode: str
    enabled: bool = True
    command: str | None = None
    api_protocol: str | None = None
    provider_id: str | None = None
    model: str | None = None
    base_url: str | None = None
    credential_ref: str | None = None
    permission_preset: str = "workspace_write"
    native_auth_readonly: bool = True


class ExecutorProfileListResponse(BaseModel):
    profiles: list[dict[str, Any]]
    support_matrix: dict[str, Any]


@router.get("")
def list_executor_profiles(
    config_service: AppConfigService = Depends(get_app_config_service),
) -> ExecutorProfileListResponse:
    stored_profiles = config_service.list_executor_profiles()
    profiles = stored_profiles or [p.model_dump() for p in default_profiles()]

    command_status: dict[str, bool] = {}
    for worker_type in sorted(SUPPORTED_AUTH_MODES.keys()):
        command_status[worker_type] = bool(
            config_service.resolve_executor_command(worker_type)
            or getattr(config_service.settings, f"{worker_type}_command_json", None)
        )

    return ExecutorProfileListResponse(
        profiles=profiles,
        support_matrix={
            "auth_modes": {k: sorted(v) for k, v in SUPPORTED_AUTH_MODES.items()},
            "api_protocols": {k: sorted(v) for k, v in SUPPORTED_API_PROTOCOLS.items()},
            "command_configured": command_status,
        },
    )


@router.post("/validate")
def validate_executor_profile(
    request: ExecutorProfileRequest,
    config_service: AppConfigService = Depends(get_app_config_service),
) -> dict[str, Any]:
    try:
        spec = ExecutorProfileSpec(**request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid executor profile: {exc}") from exc

    result = validate_profile(spec, settings=config_service.settings)
    return result.model_dump()


@router.put("/{profile_id}")
def save_executor_profile(
    profile_id: str,
    request: ExecutorProfileRequest,
    config_service: AppConfigService = Depends(get_app_config_service),
) -> dict[str, Any]:
    if request.profile_id != profile_id:
        raise HTTPException(status_code=400, detail="profile_id in path and body must match.")
    try:
        spec = ExecutorProfileSpec(**request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid executor profile: {exc}") from exc

    result = validate_profile(spec, settings=config_service.settings)
    if not result.valid:
        raise HTTPException(status_code=400, detail={"errors": result.errors, "warnings": result.warnings})

    config_service.save_executor_profile(spec.model_dump())
    return {"profile": spec.model_dump(), "validation": result.model_dump()}


@router.delete("/{profile_id}")
def delete_executor_profile(
    profile_id: str,
    config_service: AppConfigService = Depends(get_app_config_service),
) -> dict[str, Any]:
    config_service.delete_executor_profile(profile_id)
    return {"profile_id": profile_id, "deleted": True}
