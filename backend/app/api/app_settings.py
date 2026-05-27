from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_app_config_service
from app.services.app_config_service import AppConfigService

router = APIRouter(prefix="/app-settings", tags=["app-settings"])


class UpdateAppSettingsRequest(BaseModel):
    deepseek_api_key: str | None = None
    clear_deepseek_api_key: bool = False
    deepseek_api_base_url: str | None = None
    pi_deepseek_base_url: str | None = None
    manager_model: str | None = None
    executor_model: str | None = None
    reviewer_model: str | None = None
    library_summarizer_model: str | None = None
    manager_websearch_enabled: bool | None = None
    tavily_api_key: str | None = None
    clear_tavily_api_key: bool = False
    tavily_base_url: str | None = None
    anthropic_api_key: str | None = None
    clear_anthropic_api_key: bool = False
    anthropic_api_base_url: str | None = None
    openai_api_key: str | None = None
    clear_openai_api_key: bool = False
    openai_api_base_url: str | None = None


@router.get("")
def get_app_settings(config_service: AppConfigService = Depends(get_app_config_service)) -> dict[str, Any]:
    return config_service.get_public_settings()


@router.put("")
def update_app_settings(
    request: UpdateAppSettingsRequest,
    config_service: AppConfigService = Depends(get_app_config_service),
) -> dict[str, Any]:
    return config_service.update_settings(request.model_dump(exclude_unset=True))
