from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.workers.provider_renderers.base import ProviderCapabilityInputs, ProviderRenderResult, ProviderRenderer, resolve_capability_inputs, resolve_host_auth_path


class OpenCodeRenderer(ProviderRenderer):
    """Renders OpenCode CLI command and configuration.

    Supports both cli_native (host-side auth) and project_api modes.
    In project_api mode, generates a run-scoped opencode.json config.
    """

    worker_type = "opencode"

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
            return self._render_cli_native(
                profile=profile,
                prompt_path=prompt_path,
                run_dir=run_dir,
                project_root=project_root,
                settings=settings,
                packet=packet,
            )
        if auth_mode == "project_api":
            return self._render_project_api(
                profile=profile,
                prompt_path=prompt_path,
                run_dir=run_dir,
                project_root=project_root,
                settings=settings,
                packet=packet,
            )
        return self.render_unsupported(
            auth_mode=auth_mode,
            error=f"OpenCode does not support auth_mode={auth_mode}.",
        )

    def _render_cli_native(
        self,
        *,
        profile: Any,
        prompt_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: Any,
        packet: dict[str, Any] | None = None,
    ) -> ProviderRenderResult:
        capabilities = resolve_capability_inputs(packet=packet, run_dir=run_dir)
        config_path = self._write_capability_config(
            run_dir=run_dir,
            auth_mode="cli_native",
            provider_config={},
            capabilities=capabilities,
        )
        command_argv = [
            "opencode",
            "run",
            "--file",
            str(prompt_path),
            "--format",
            "json",
            "--dangerously-skip-permissions",
            f"Read {prompt_path} and complete the Blueprint executor contract exactly.",
        ]

        env_overlay: dict[str, str] = {}
        env_overlay.update(self._capability_env(capabilities, config_path=config_path))

        # Point OpenCode at the real host auth/config path so it can find host-side login state
        # even though bwrap rewrites HOME/XDG_CONFIG_HOME to run-local dirs
        host_auth_path = resolve_host_auth_path("opencode")
        if host_auth_path:
            env_overlay["OPENCODE_CONFIG_DIR"] = str(host_auth_path)

        provider_config_plan = {
            "provider_id": None,
            "api_protocol": None,
            "model": None,
            "base_url": None,
            "credential_ref": None,
            "credential_injected": False,
            "note": "cli_native: OpenCode uses host-side auth/config. OPENCODE_CONFIG_DIR points to real host auth path if available.",
            "host_auth_path": str(host_auth_path) if host_auth_path else None,
            "capabilities": self._capability_summary(capabilities, config_path=config_path),
        }

        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode="cli_native",
            command_argv=command_argv,
            environment_overlay=env_overlay,
            config_file_paths=[str(config_path)],
            config_summary={"blueprint_opencode_capabilities.json": self._config_summary(config_path)},
            redacted_command=self.redact_command(command_argv),
            provider_config_plan=provider_config_plan,
        )

    def _render_project_api(
        self,
        *,
        profile: Any,
        prompt_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: Any,
        packet: dict[str, Any] | None = None,
    ) -> ProviderRenderResult:
        capabilities = resolve_capability_inputs(packet=packet, run_dir=run_dir)
        model = (
            getattr(profile, "model", None)
            or getattr(settings, "opencode_executor_model", None)
            or getattr(settings, "executor_model", "gpt-4o-mini")
        )
        api_protocol = (
            getattr(profile, "api_protocol", None)
            or getattr(settings, "opencode_api_protocol", None)
            or "anthropic_compatible"
        )
        if api_protocol == "anthropic_compatible":
            base_url = (
                getattr(profile, "base_url", None)
                or getattr(settings, "opencode_api_base_url", None)
                or getattr(settings, "anthropic_api_base_url", "https://api.anthropic.com")
            )
            provider_id = getattr(profile, "provider_id", None) or "anthropic"
        else:
            base_url = (
                getattr(profile, "base_url", None)
                or getattr(settings, "opencode_api_base_url", None)
                or getattr(settings, "openai_api_base_url", "https://api.openai.com/v1")
            )
            provider_id = getattr(profile, "provider_id", None) or "openai"

        credential_ref = getattr(profile, "credential_ref", None) or (
            "project:opencode_api_key" if api_protocol == "anthropic_compatible" else "project:openai_api_key"
        )
        if not model:
            return self.render_unsupported(
                auth_mode="project_api",
                error="OpenCode project API mode requires a model name.",
            )
        if not str(base_url or "").strip():
            return self.render_unsupported(
                auth_mode="project_api",
                error="OpenCode project API mode requires a base_url.",
            )
        if not credential_ref:
            return self.render_unsupported(
                auth_mode="project_api",
                error="OpenCode project API mode requires credential_ref to inject an API key.",
            )

        resolved_key = self._resolve_credential(credential_ref, settings)
        if not resolved_key:
            return self.render_unsupported(
                auth_mode="project_api",
                error=f"OpenCode project API mode could not resolve credential_ref={credential_ref}.",
            )

        env_overlay: dict[str, str] = {
            "ANTHROPIC_API_KEY" if api_protocol == "anthropic_compatible" else "OPENAI_API_KEY": resolved_key,
        }
        credential_injected = True

        config_content = self._build_opencode_provider_config(
            provider_id=provider_id,
            model=model,
            base_url=base_url,
            api_protocol=api_protocol,
        )
        config_path = self._write_capability_config(
            run_dir=run_dir,
            auth_mode="project_api",
            provider_config=config_content,
            capabilities=capabilities,
        )
        env_overlay["OPENCODE_CONFIG_DIR"] = str(config_path.parent)
        env_overlay.update(self._capability_env(capabilities, config_path=config_path))

        command_argv = [
            "opencode",
            "run",
            "--file",
            str(prompt_path),
            "--format",
            "json",
            "--dangerously-skip-permissions",
            f"Read {prompt_path} and complete the Blueprint executor contract exactly.",
        ]

        provider_config_plan = {
            "provider_id": provider_id,
            "api_protocol": api_protocol,
            "model": model,
            "base_url": base_url,
            "credential_ref": credential_ref,
            "credential_injected": credential_injected,
            "capabilities": self._capability_summary(capabilities, config_path=config_path),
        }

        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode="project_api",
            command_argv=command_argv,
            environment_overlay=env_overlay,
            config_file_paths=[str(config_path)],
            config_summary={"blueprint_opencode_capabilities.json": self._config_summary(config_path)},
            redacted_command=self.redact_command(command_argv),
            provider_config_plan=provider_config_plan,
        )

    @staticmethod
    def _resolve_credential(credential_ref: str, settings: Any) -> str | None:
        if not credential_ref:
            return None
        if credential_ref.startswith("project:"):
            key_name = credential_ref.removeprefix("project:")
            value = getattr(settings, key_name, None)
            if value is None:
                return None
            if hasattr(value, "get_secret_value"):
                return value.get_secret_value()
            return str(value) if value else None
        return None

    @staticmethod
    def _write_capability_config(
        *,
        run_dir: Path,
        auth_mode: str,
        provider_config: dict[str, Any],
        capabilities: ProviderCapabilityInputs,
    ) -> Path:
        config_dir = run_dir / "opencode-config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "opencode.json"
        config_content = {
            "$schema": "https://opencode.ai/config.json",
            **provider_config,
            "skills": {
                "ids": list(capabilities.skills),
                "bindings_file": str(capabilities.skill_bindings_path) if capabilities.skill_bindings_path else None,
                "paths": [str(path) for path in capabilities.skill_run_paths],
            },
            "mcp": {
                "ids": list(capabilities.mcp_servers),
                "bindings_file": str(capabilities.mcp_bindings_path) if capabilities.mcp_bindings_path else None,
                "config_file": str(capabilities.mcp_config_path) if capabilities.mcp_config_path else None,
            },
            "tool_policy": dict(capabilities.tool_policy),
            "blueprint": {
                "schema_version": "blueprint.opencode.capabilities.v1",
                "auth_mode": auth_mode,
            },
        }
        if capabilities.mcp_config_path and capabilities.mcp_config_path.exists():
            try:
                config_content["mcp"]["config"] = json.loads(capabilities.mcp_config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                config_content["mcp"]["config"] = None
        config_path.write_text(json.dumps(config_content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return config_path

    @staticmethod
    def _capability_env(capabilities: ProviderCapabilityInputs, *, config_path: Path) -> dict[str, str]:
        env = {
            "BLUEPRINT_OPENCODE_CAPABILITY_CONFIG": str(config_path),
        }
        if capabilities.skill_bindings_path:
            env["BLUEPRINT_EXECUTOR_SKILL_BINDINGS"] = str(capabilities.skill_bindings_path)
        if capabilities.mcp_bindings_path:
            env["BLUEPRINT_EXECUTOR_MCP_BINDINGS"] = str(capabilities.mcp_bindings_path)
        if capabilities.mcp_config_path:
            env["BLUEPRINT_EXECUTOR_MCP_CONFIG"] = str(capabilities.mcp_config_path)
            env["OPENCODE_MCP_CONFIG"] = str(capabilities.mcp_config_path)
        if capabilities.skill_run_paths:
            env["BLUEPRINT_EXECUTOR_SKILL_PATHS"] = json.dumps([str(path) for path in capabilities.skill_run_paths])
        return env

    @staticmethod
    def _capability_summary(capabilities: ProviderCapabilityInputs, *, config_path: Path) -> dict[str, Any]:
        return {
            "skills": list(capabilities.skills),
            "mcp_servers": list(capabilities.mcp_servers),
            "tool_policy": dict(capabilities.tool_policy),
            "capability_config": str(config_path),
            "native_mcp_config": bool(capabilities.mcp_config_path),
        }

    @staticmethod
    def _config_summary(config_path: Path) -> dict[str, Any]:
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"path": str(config_path), "invalid": True}

    @staticmethod
    def _build_opencode_provider_config(
        *,
        provider_id: str,
        model: str,
        base_url: str,
        api_protocol: str,
    ) -> dict[str, Any]:
        if api_protocol == "anthropic_compatible":
            provider_id = provider_id or "anthropic"
            return {
                "provider": {
                    provider_id: {
                        "name": provider_id,
                        "options": {"baseURL": OpenCodeRenderer._anthropic_v1_base_url(base_url)},
                        "models": {model: {"name": model}},
                    }
                },
                "model": f"{provider_id}/{model}",
            }
        if api_protocol == "provider_native":
            return {
                "provider": {
                    provider_id: {
                        "name": provider_id,
                        "options": {"baseURL": base_url},
                        "models": {model: {"name": model}},
                    }
                },
                "model": f"{provider_id}/{model}",
            }
        return {
            "provider": {
                provider_id: {
                    "name": provider_id,
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": base_url},
                    "models": {model: {"name": model}},
                }
            },
            "model": f"{provider_id}/{model}",
        }

    @staticmethod
    def _anthropic_v1_base_url(base_url: str) -> str:
        value = str(base_url or "").rstrip("/")
        if value.endswith("/v1"):
            return value
        return f"{value}/v1"
