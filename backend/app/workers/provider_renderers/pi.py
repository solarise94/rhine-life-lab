from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workers.provider_renderers.base import ProviderRenderResult, ProviderRenderer, resolve_host_auth_path


class PiRenderer(ProviderRenderer):
    """Renders Pi CLI command and configuration.

    Pi supports project_api injection and cli_native host-side login.
    """

    worker_type = "pi"

    def render(
        self,
        *,
        auth_mode: str,
        profile: Any,
        prompt_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: Any,
        packet: dict[str, Any] | None = None,
    ) -> ProviderRenderResult:
        if auth_mode == "cli_native":
            return self._render_cli_native(auth_mode=auth_mode)
        if auth_mode != "project_api":
            return self.render_unsupported(auth_mode=auth_mode, error=f"Pi does not support auth_mode={auth_mode}.")

        env_overlay: dict[str, str] = {}
        api_key = getattr(settings, "pi_api_key", None) or getattr(settings, "deepseek_api_key", None)
        if api_key:
            env_overlay["BLUEPRINT_DEEPSEEK_API_KEY"] = api_key.get_secret_value()
        env_overlay["BLUEPRINT_DEEPSEEK_API_BASE_URL"] = str(
            getattr(settings, "pi_anthropic_base_url", None) or getattr(settings, "deepseek_api_base_url", "")
        )
        env_overlay["BLUEPRINT_PI_DEEPSEEK_BASE_URL"] = str(getattr(settings, "pi_deepseek_base_url", "https://api.deepseek.com"))
        env_overlay["BLUEPRINT_MANAGER_MODEL"] = str(getattr(settings, "manager_model", "deepseek-v4-pro"))
        env_overlay["BLUEPRINT_EXECUTOR_MODEL"] = str(
            getattr(settings, "pi_executor_model", None)
            or getattr(settings, "executor_model", getattr(settings, "manager_model", "deepseek-v4-flash"))
        )
        env_overlay["BLUEPRINT_REVIEWER_MODEL"] = str(
            getattr(settings, "reviewer_model", getattr(settings, "manager_model", "deepseek-v4-flash"))
        )
        env_overlay["BLUEPRINT_MANAGER_TEMPERATURE"] = str(getattr(settings, "manager_temperature", 0.2))
        env_overlay["BLUEPRINT_MANAGER_MAX_TOKENS"] = str(getattr(settings, "manager_max_tokens", 2400))
        env_overlay["BLUEPRINT_MANAGER_TIMEOUT_SECONDS"] = str(getattr(settings, "manager_timeout_seconds", 600))

        command_argv: list[str] = []

        provider_config_plan = {
            "provider_id": "deepseek",
            "api_protocol": "deepseek_compatible",
            "model": env_overlay.get("BLUEPRINT_EXECUTOR_MODEL"),
            "base_url": env_overlay.get("BLUEPRINT_PI_DEEPSEEK_BASE_URL"),
            "credential_ref": "project:deepseek_api_key",
            "credential_injected": bool(api_key),
        }

        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode=auth_mode,
            command_argv=command_argv,
            environment_overlay=env_overlay,
            redacted_command=self.redact_command(command_argv),
            provider_config_plan=provider_config_plan,
        )

    def _render_cli_native(self, *, auth_mode: str) -> ProviderRenderResult:
        host_auth_path = resolve_host_auth_path("pi")
        env_overlay: dict[str, str] = {}
        if host_auth_path:
            env_overlay["PI_CODING_AGENT_DIR"] = str(host_auth_path)

        command_argv: list[str] = []
        provider_config_plan = {
            "provider_id": None,
            "api_protocol": None,
            "model": None,
            "base_url": None,
            "credential_ref": None,
            "credential_injected": False,
            "host_auth_path": str(host_auth_path) if host_auth_path else None,
            "note": "cli_native: Pi uses host-side ~/.pi/agent auth.json or provider env. Wrapper does not inject project API credentials.",
        }
        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode=auth_mode,
            command_argv=command_argv,
            environment_overlay=env_overlay,
            redacted_command=self.redact_command(command_argv),
            provider_config_plan=provider_config_plan,
        )
