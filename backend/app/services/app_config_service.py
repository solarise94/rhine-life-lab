from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.models.executor_profiles import default_profiles


class AppConfigService:
    """Stores UI-managed app configuration without exposing secrets back to clients."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.path = Path(self.settings.data_root) / "_app_settings.json"
        self._cache_lock = RLock()
        self._cache_mtime_ns: int | None = None
        self._cache_payload: dict[str, Any] | None = None
        self._apply_runtime_overrides(self._load())

    def get_public_settings(self) -> dict[str, Any]:
        config = self._load()
        deepseek_key = self._effective_deepseek_api_key(config)
        tavily_key = self._effective_tavily_api_key(config)
        anthropic_key = self._effective_anthropic_api_key(config)
        openai_key = self._effective_openai_api_key(config)
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
                "library_summarizer_model": str(
                    config.get("library_summarizer_model") or self.settings.library_summarizer_model
                ),
            },
            "web_search": {
                "enabled": websearch_enabled,
                "api_key_configured": bool(tavily_key),
                "base_url": str(config.get("tavily_base_url") or os.environ.get("TAVILY_BASE_URL") or "https://api.tavily.com"),
            },
            "anthropic": {
                "api_key_configured": bool(anthropic_key),
                "api_base_url": str(config.get("anthropic_api_base_url") or self.settings.anthropic_api_base_url),
            },
            "openai": {
                "api_key_configured": bool(openai_key),
                "api_base_url": str(config.get("openai_api_base_url") or self.settings.openai_api_base_url),
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
            "library_summarizer_model",
            "tavily_base_url",
            "anthropic_api_base_url",
            "openai_api_base_url",
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

        if payload.get("clear_anthropic_api_key"):
            config.pop("anthropic_api_key", None)
        elif "anthropic_api_key" in payload and payload["anthropic_api_key"] is not None:
            value = str(payload["anthropic_api_key"]).strip()
            if value:
                config["anthropic_api_key"] = value

        if payload.get("clear_openai_api_key"):
            config.pop("openai_api_key", None)
        elif "openai_api_key" in payload and payload["openai_api_key"] is not None:
            value = str(payload["openai_api_key"]).strip()
            if value:
                config["openai_api_key"] = value

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

    def get_secret_settings(self) -> dict[str, Any]:
        config = self._load()
        return {
            "deepseek_api_key": self._effective_deepseek_api_key(config),
            "deepseek_api_base_url": str(config.get("deepseek_api_base_url") or self.settings.deepseek_api_base_url),
            "pi_deepseek_base_url": str(config.get("pi_deepseek_base_url") or self.settings.pi_deepseek_base_url),
            "manager_model": str(config.get("manager_model") or self.settings.manager_model),
            "executor_model": str(config.get("executor_model") or self.settings.executor_model),
            "reviewer_model": str(config.get("reviewer_model") or self.settings.reviewer_model),
            "library_summarizer_model": str(
                config.get("library_summarizer_model") or self.settings.library_summarizer_model
            ),
            "manager_websearch_enabled": self._effective_bool(
                config.get("manager_websearch_enabled"),
                os.environ.get("MANAGER_WEBSEARCH_ENABLED"),
                default=False,
            ),
            "tavily_api_key": self._effective_tavily_api_key(config),
            "tavily_base_url": str(config.get("tavily_base_url") or os.environ.get("TAVILY_BASE_URL") or "https://api.tavily.com"),
            "anthropic_api_key": self._effective_anthropic_api_key(config),
            "anthropic_api_base_url": str(config.get("anthropic_api_base_url") or self.settings.anthropic_api_base_url),
            "openai_api_key": self._effective_openai_api_key(config),
            "openai_api_base_url": str(config.get("openai_api_base_url") or self.settings.openai_api_base_url),
        }

    def _load(self) -> dict[str, Any]:
        with self._cache_lock:
            try:
                mtime_ns = self.path.stat().st_mtime_ns
            except OSError:
                self._cache_mtime_ns = None
                self._cache_payload = {}
                return {}
            if self._cache_payload is not None and self._cache_mtime_ns == mtime_ns:
                return dict(self._cache_payload)
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            self._cache_mtime_ns = mtime_ns
            self._cache_payload = dict(payload)
            return dict(payload)

    def _save(self, payload: dict[str, Any]) -> None:
        with self._cache_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
            try:
                self._cache_mtime_ns = self.path.stat().st_mtime_ns
            except OSError:
                self._cache_mtime_ns = None
            self._cache_payload = dict(payload)

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
        if config.get("library_summarizer_model"):
            self.settings.library_summarizer_model = str(config["library_summarizer_model"])
        if config.get("anthropic_api_base_url"):
            self.settings.anthropic_api_base_url = str(config["anthropic_api_base_url"])
        if config.get("openai_api_base_url"):
            self.settings.openai_api_base_url = str(config["openai_api_base_url"])
        deepseek_key = self._effective_deepseek_api_key(config)
        if deepseek_key:
            self.settings.deepseek_api_key = SecretStr(deepseek_key)
        anthropic_key = self._effective_anthropic_api_key(config)
        if anthropic_key:
            self.settings.anthropic_api_key = SecretStr(anthropic_key)
        openai_key = self._effective_openai_api_key(config)
        if openai_key:
            self.settings.openai_api_key = SecretStr(openai_key)

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

    def _effective_anthropic_api_key(self, config: dict[str, Any]) -> str:
        configured = str(config.get("anthropic_api_key") or "").strip()
        if configured:
            return configured
        if self.settings.anthropic_api_key:
            return self.settings.anthropic_api_key.get_secret_value()
        return os.environ.get("ANTHROPIC_API_KEY", "").strip()

    def _effective_openai_api_key(self, config: dict[str, Any]) -> str:
        configured = str(config.get("openai_api_key") or "").strip()
        if configured:
            return configured
        if self.settings.openai_api_key:
            return self.settings.openai_api_key.get_secret_value()
        return os.environ.get("OPENAI_API_KEY", "").strip()

    @staticmethod
    def _effective_bool(config_value: Any, env_value: str | None, *, default: bool) -> bool:
        if config_value is not None:
            return bool(config_value)
        if env_value is None:
            return default
        return env_value.lower() in {"1", "true", "yes", "on"}

    def list_executor_profiles(self) -> list[dict[str, Any]]:
        config = self._load()
        profiles = config.get("executor_profiles")
        if not isinstance(profiles, list):
            return []
        return [item for item in profiles if isinstance(item, dict)]

    def save_executor_profile(self, profile: dict[str, Any]) -> None:
        config = self._load()
        profiles = config.get("executor_profiles")
        if not isinstance(profiles, list):
            profiles = []
        profile_id = profile.get("profile_id")
        existing_index = next(
            (i for i, item in enumerate(profiles) if isinstance(item, dict) and item.get("profile_id") == profile_id),
            None,
        )
        if existing_index is not None:
            profiles[existing_index] = profile
        else:
            profiles.append(profile)
        config["executor_profiles"] = profiles
        self._save(config)

    def delete_executor_profile(self, profile_id: str) -> None:
        config = self._load()
        profiles = config.get("executor_profiles")
        if not isinstance(profiles, list):
            return
        config["executor_profiles"] = [
            item for item in profiles if not (isinstance(item, dict) and item.get("profile_id") == profile_id)
        ]
        self._save(config)

    def resolve_executor_command(self, worker_type: str) -> str | None:
        setting_name = f"{worker_type}_command"
        return getattr(self.settings, setting_name, None)

    def resolve_executor_profile(self, worker_type: str, profile_id: str | None = None) -> dict[str, Any] | None:
        stored_profiles = self.list_executor_profiles()
        default_profile_items = [profile.model_dump() for profile in default_profiles()]
        profiles = stored_profiles + [
            item for item in default_profile_items
            if not any(stored.get("profile_id") == item.get("profile_id") for stored in stored_profiles)
        ]
        if profile_id:
            return next(
                (item for item in profiles if item.get("profile_id") == profile_id and item.get("worker_type") == worker_type),
                None,
            )
        return next(
            (item for item in profiles if item.get("worker_type") == worker_type and item.get("enabled", True)),
            None,
        )
