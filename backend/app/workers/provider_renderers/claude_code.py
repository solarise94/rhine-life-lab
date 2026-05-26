from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workers.provider_renderers.base import ProviderCapabilityInputs, ProviderRenderResult, ProviderRenderer, resolve_capability_inputs, resolve_host_auth_path


class ClaudeCodeRenderer(ProviderRenderer):
    """Renders Claude Code CLI command and configuration.

    Supports cli_native (host-side login) only.
    Project API injection is intentionally disabled for Claude Code because the
    native subscription/config path is materially different from API-key mode.
    """

    worker_type = "claude_code"

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
            return self.render_unsupported(
                auth_mode="project_api",
                error="Claude Code project API injection is not supported. Use cli_native with local Claude login.",
            )
        return self.render_unsupported(
            auth_mode=auth_mode,
            error=f"Claude Code does not support auth_mode={auth_mode}.",
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
        command_argv = [
            "claude",
            "-p",
            f"@{prompt_path}",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        command_argv.extend(self._capability_argv(capabilities))

        env_overlay: dict[str, str] = {}
        env_overlay.update(self._capability_env(capabilities))

        # Point Claude at the real host auth/config path so it can find host-side login state
        # even though bwrap rewrites HOME/XDG_CONFIG_HOME to run-local dirs
        host_auth_path = resolve_host_auth_path("claude")
        if host_auth_path:
            env_overlay["CLAUDE_CONFIG_DIR"] = str(host_auth_path)

        provider_config_plan = {
            "provider_id": None,
            "api_protocol": None,
            "model": None,
            "base_url": None,
            "credential_ref": None,
            "credential_injected": False,
            "note": "cli_native: Claude Code uses host-side login state. CLAUDE_CONFIG_DIR points to the host home/config root if available.",
            "host_auth_path": str(host_auth_path) if host_auth_path else None,
            "capabilities": self._capability_summary(capabilities),
        }

        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode="cli_native",
            command_argv=command_argv,
            environment_overlay=env_overlay,
            redacted_command=self.redact_command(command_argv),
            provider_config_plan=provider_config_plan,
        )

    @staticmethod
    def _capability_env(capabilities: ProviderCapabilityInputs) -> dict[str, str]:
        env: dict[str, str] = {}
        if capabilities.skill_bindings_path:
            env["BLUEPRINT_EXECUTOR_SKILL_BINDINGS"] = str(capabilities.skill_bindings_path)
        if capabilities.mcp_bindings_path:
            env["BLUEPRINT_EXECUTOR_MCP_BINDINGS"] = str(capabilities.mcp_bindings_path)
        if capabilities.mcp_config_path:
            env["BLUEPRINT_EXECUTOR_MCP_CONFIG"] = str(capabilities.mcp_config_path)
        if capabilities.skill_run_paths:
            env["BLUEPRINT_EXECUTOR_SKILL_PATHS"] = json_dumps_paths(capabilities.skill_run_paths)
        return env

    @staticmethod
    def _capability_argv(capabilities: ProviderCapabilityInputs) -> list[str]:
        argv: list[str] = []
        if capabilities.has_mcp_config and capabilities.mcp_config_path:
            argv.extend(["--mcp-config", str(capabilities.mcp_config_path)])

        policy = capabilities.tool_policy
        if policy:
            if policy.get("shell") is False:
                argv.extend(["--disallowedTools", "Bash"])
            if policy.get("git_write") is False:
                argv.extend(["--disallowedTools", "Bash(git add *),Bash(git commit *),Bash(git push *)"])
            if policy.get("network") == "allow":
                argv.extend(["--permission-mode", "acceptEdits"])
            elif policy.get("network") == "deny":
                argv.extend(["--permission-mode", "default"])
        return argv

    @staticmethod
    def _capability_summary(capabilities: ProviderCapabilityInputs) -> dict[str, Any]:
        return {
            "skills": list(capabilities.skills),
            "mcp_servers": list(capabilities.mcp_servers),
            "tool_policy": dict(capabilities.tool_policy),
            "skill_bindings": str(capabilities.skill_bindings_path) if capabilities.skill_bindings_path else None,
            "mcp_config": str(capabilities.mcp_config_path) if capabilities.mcp_config_path else None,
            "native_mcp_config": capabilities.has_mcp_config,
        }


def json_dumps_paths(paths: list[Path]) -> str:
    import json

    return json.dumps([str(path) for path in paths])
