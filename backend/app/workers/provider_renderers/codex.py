from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workers.provider_renderers.base import ProviderRenderResult, ProviderRenderer, resolve_host_auth_path


class CodexRenderer(ProviderRenderer):
    """Renders Codex CLI command and configuration.

    Only cli_native is supported. project_api mode is explicitly blocked
    until a dedicated OpenAI-compatible Codex renderer is built.
    """

    worker_type = "codex"

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
        if auth_mode == "project_api":
            return self.render_unsupported(
                auth_mode="project_api",
                error=(
                    "Codex project API mode is not implemented yet. "
                    "Use cli_native with host-side Codex login. "
                    "A dedicated OpenAI-compatible config renderer is required before project_api can be supported."
                ),
            )
        if auth_mode != "cli_native":
            return self.render_unsupported(
                auth_mode=auth_mode,
                error=f"Codex only supports cli_native auth mode, got {auth_mode}.",
            )

        return self._render_cli_native(
            profile=profile,
            prompt_path=prompt_path,
            run_dir=run_dir,
            project_root=project_root,
            settings=settings,
        )

    def _render_cli_native(
        self,
        *,
        profile: Any,
        prompt_path: Path,
        run_dir: Path,
        project_root: Path,
        settings: Any,
    ) -> ProviderRenderResult:
        command_argv = [
            "codex",
            "exec",
            "--full-auto",
            str(prompt_path),
        ]

        env_overlay: dict[str, str] = {}

        # Point Codex at the real host auth/config path so it can find host-side login state
        # even though bwrap rewrites HOME/XDG_CONFIG_HOME to run-local dirs
        host_auth_path = resolve_host_auth_path("codex")
        if host_auth_path:
            env_overlay["CODEX_CONFIG_DIR"] = str(host_auth_path)

        provider_config_plan = {
            "provider_id": None,
            "api_protocol": None,
            "model": None,
            "base_url": None,
            "credential_ref": None,
            "credential_injected": False,
            "note": "cli_native: Codex uses host-side ChatGPT/Codex login. CODEX_CONFIG_DIR points to real host auth path if available.",
            "host_auth_path": str(host_auth_path) if host_auth_path else None,
        }

        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode="cli_native",
            command_argv=command_argv,
            environment_overlay=env_overlay,
            redacted_command=self.redact_command(command_argv),
            provider_config_plan=provider_config_plan,
        )
