from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.core.config import Settings, get_settings


class AppConfigService:
    """Stores UI-managed app configuration without exposing secrets back to clients."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.path = Path(self.settings.data_root) / "_app_settings.json"
        self._apply_runtime_overrides(self._load())

    def get_public_settings(self) -> dict[str, Any]:
        config = self._load()
        deepseek_key = self._effective_deepseek_api_key(config)
        tavily_key = self._effective_tavily_api_key(config)
        websearch_enabled = self._effective_bool(
            config.get("manager_websearch_enabled"),
            os.environ.get("MANAGER_WEBSEARCH_ENABLED"),
            default=False,
        )
        return {
            "deepseek": {
                "api_key_configured": bool(deepseek_key),
                "api_base_url": str(config.get("deepseek_api_base_url") or self.settings.deepseek_api_base_url),
                "pi_base_url": str(config.get("pi_deepseek_base_url") or self.settings.pi_deepseek_base_url),
                "manager_model": str(config.get("manager_model") or self.settings.manager_model),
                "executor_model": str(config.get("executor_model") or self.settings.executor_model),
                "reviewer_model": str(config.get("reviewer_model") or self.settings.reviewer_model),
            },
            "web_search": {
                "enabled": websearch_enabled,
                "api_key_configured": bool(tavily_key),
                "base_url": str(config.get("tavily_base_url") or os.environ.get("TAVILY_BASE_URL") or "https://api.tavily.com"),
            },
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self._load()
        string_fields = {
            "deepseek_api_base_url",
            "pi_deepseek_base_url",
            "manager_model",
            "executor_model",
            "reviewer_model",
            "tavily_base_url",
        }
        for field in string_fields:
            if field in payload and payload[field] is not None:
                value = str(payload[field]).strip()
                if value:
                    config[field] = value

        if "manager_websearch_enabled" in payload:
            config["manager_websearch_enabled"] = bool(payload["manager_websearch_enabled"])

        if payload.get("clear_deepseek_api_key"):
            config.pop("deepseek_api_key", None)
        elif "deepseek_api_key" in payload and payload["deepseek_api_key"] is not None:
            value = str(payload["deepseek_api_key"]).strip()
            if value:
                config["deepseek_api_key"] = value

        if payload.get("clear_tavily_api_key"):
            config.pop("tavily_api_key", None)
        elif "tavily_api_key" in payload and payload["tavily_api_key"] is not None:
            value = str(payload["tavily_api_key"]).strip()
            if value:
                config["tavily_api_key"] = value

        self._save(config)
        self._apply_runtime_overrides(config)
        return self.get_public_settings()

    def manager_agent_config(self, *, include_secrets: bool = False) -> dict[str, Any]:
        config = self._load()
        payload = {
            "provider": os.environ.get("MANAGER_AGENT_PROVIDER") or "deepseek",
            "model": str(config.get("manager_model") or self.settings.manager_model),
            "deepseek_api_base_url": str(config.get("deepseek_api_base_url") or self.settings.deepseek_api_base_url),
            "pi_deepseek_base_url": str(config.get("pi_deepseek_base_url") or self.settings.pi_deepseek_base_url),
            "websearch_enabled": self._effective_bool(
                config.get("manager_websearch_enabled"),
                os.environ.get("MANAGER_WEBSEARCH_ENABLED"),
                default=False,
            ),
            "tavily_base_url": str(config.get("tavily_base_url") or os.environ.get("TAVILY_BASE_URL") or "https://api.tavily.com"),
        }
        if include_secrets:
            payload["api_key"] = self._effective_deepseek_api_key(config)
            payload["tavily_api_key"] = self._effective_tavily_api_key(config)
        return payload

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _apply_runtime_overrides(self, config: dict[str, Any]) -> None:
        if config.get("deepseek_api_base_url"):
            self.settings.deepseek_api_base_url = str(config["deepseek_api_base_url"])
        if config.get("pi_deepseek_base_url"):
            self.settings.pi_deepseek_base_url = str(config["pi_deepseek_base_url"])
        if config.get("manager_model"):
            self.settings.manager_model = str(config["manager_model"])
        if config.get("executor_model"):
            self.settings.executor_model = str(config["executor_model"])
        if config.get("reviewer_model"):
            self.settings.reviewer_model = str(config["reviewer_model"])
        deepseek_key = self._effective_deepseek_api_key(config)
        if deepseek_key:
            self.settings.deepseek_api_key = SecretStr(deepseek_key)

    def _effective_deepseek_api_key(self, config: dict[str, Any]) -> str:
        configured = str(config.get("deepseek_api_key") or "").strip()
        if configured:
            return configured
        if self.settings.deepseek_api_key:
            return self.settings.deepseek_api_key.get_secret_value()
        return os.environ.get("BLUEPRINT_DEEPSEEK_API_KEY", "").strip()

    @staticmethod
    def _effective_tavily_api_key(config: dict[str, Any]) -> str:
        configured = str(config.get("tavily_api_key") or "").strip()
        if configured:
            return configured
        return os.environ.get("TAVILY_API_KEY", "").strip()

    @staticmethod
    def _effective_bool(config_value: Any, env_value: str | None, *, default: bool) -> bool:
        if config_value is not None:
            return bool(config_value)
        if env_value is None:
            return default
        return env_value.lower() in {"1", "true", "yes", "on"}
