from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json
import os
import re


SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|credential|auth)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(r"sk-[A-Za-z0-9_-]+")


def resolve_host_auth_path(cli_name: str) -> Path | None:
    """Resolve the host-side auth/config path for a CLI.

    Returns the real host path (before bwrap rewrites HOME/XDG vars).
    Returns None if the path doesn't exist or can't be determined.
    """
    # Check for CLI-specific env vars first (explicit overrides)
    env_map = {
        "claude": "CLAUDE_CONFIG_DIR",
        "opencode": "OPENCODE_CONFIG_DIR",
        "codex": "CODEX_CONFIG_DIR",
    }
    env_var = env_map.get(cli_name)
    if env_var and os.environ.get(env_var):
        path = Path(os.environ[env_var])
        if path.exists():
            return path

    # Check for host-side CLI-specific env vars (passed through bwrap)
    host_env_map = {
        "claude": "BLUEPRINT_HOST_CLAUDE_CONFIG_DIR",
        "opencode": "BLUEPRINT_HOST_OPENCODE_CONFIG_DIR",
        "codex": "BLUEPRINT_HOST_CODEX_CONFIG_DIR",
    }
    host_env_var = host_env_map.get(cli_name)
    if host_env_var and os.environ.get(host_env_var):
        path = Path(os.environ[host_env_var])
        if path.exists():
            return path

    # CLI-specific fallback paths before generic ~/.config
    host_xdg_config = os.environ.get("BLUEPRINT_HOST_XDG_CONFIG_HOME")
    host_home = os.environ.get("BLUEPRINT_HOST_HOME")

    candidates: list[Path] = []
    if cli_name == "codex" and host_home:
        candidates.append(Path(host_home) / ".codex")
    if cli_name == "claude" and host_home:
        candidates.append(Path(host_home))
        candidates.append(Path(host_home) / ".claude")
    if cli_name == "opencode" and host_xdg_config:
        candidates.append(Path(host_xdg_config) / "opencode")
    if cli_name == "opencode" and host_home:
        candidates.append(Path(host_home) / ".config" / "opencode")

    for path in candidates:
        if path.exists():
            return path

    # Generic fallback to host XDG_CONFIG_HOME or host HOME/.config
    if host_xdg_config:
        base = Path(host_xdg_config)
    elif host_home:
        base = Path(host_home) / ".config"
    else:
        # No host paths available, fall back to current (sandboxed) environment
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            base = Path(xdg_config)
        else:
            home = os.environ.get("HOME") or Path.home()
            base = Path(home) / ".config"

    # CLI-specific subdirectories
    subdir_map = {
        "claude": "claude",
        "opencode": "opencode",
        "codex": "codex",
    }
    subdir = subdir_map.get(cli_name, cli_name)
    path = base / subdir
    return path if path.exists() else None


@dataclass(frozen=True)
class ProviderCapabilityInputs:
    """Run-local capability files and policy derived from task_packet.json."""

    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    tool_policy: dict[str, Any] = field(default_factory=dict)
    skill_bindings_path: Path | None = None
    mcp_bindings_path: Path | None = None
    mcp_config_path: Path | None = None
    skill_run_paths: list[Path] = field(default_factory=list)

    @property
    def has_mcp_config(self) -> bool:
        return bool(self.mcp_servers and self.mcp_config_path and self.mcp_config_path.exists())

    @property
    def has_skill_bindings(self) -> bool:
        return bool(self.skills and self.skill_bindings_path and self.skill_bindings_path.exists())


def resolve_capability_inputs(*, packet: dict[str, Any] | None, run_dir: Path) -> ProviderCapabilityInputs:
    """Resolve provider-neutral executor capabilities from the run packet."""
    executor_context = packet.get("executor_context") if isinstance(packet, dict) else {}
    if not isinstance(executor_context, dict):
        executor_context = {}

    skills = [str(item) for item in executor_context.get("skills") or [] if str(item)]
    mcp_servers = [str(item) for item in executor_context.get("mcp_servers") or [] if str(item)]
    tool_policy = executor_context.get("tool_policy") if isinstance(executor_context.get("tool_policy"), dict) else {}

    library_root = run_dir / "library"
    skill_bindings_path = library_root / "skill_bindings.json"
    mcp_bindings_path = library_root / "mcp_bindings.json"
    mcp_config_path = library_root / "mcp.json"

    skill_run_paths: list[Path] = []
    if skill_bindings_path.exists():
        try:
            bindings = json.loads(skill_bindings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            bindings = []
        if isinstance(bindings, list):
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                run_path = binding.get("run_path")
                if run_path:
                    skill_run_paths.append(Path(str(run_path)))

    return ProviderCapabilityInputs(
        skills=skills,
        mcp_servers=mcp_servers,
        tool_policy=dict(tool_policy),
        skill_bindings_path=skill_bindings_path if skill_bindings_path.exists() else None,
        mcp_bindings_path=mcp_bindings_path if mcp_bindings_path.exists() else None,
        mcp_config_path=mcp_config_path if mcp_config_path.exists() else None,
        skill_run_paths=skill_run_paths,
    )


@dataclass(frozen=True)
class ProviderRenderResult:
    """The output of a provider renderer."""

    worker_type: str
    auth_mode: str
    command_argv: list[str]
    environment_overlay: dict[str, str] = field(default_factory=dict)
    config_file_paths: list[str] = field(default_factory=list)
    config_summary: dict[str, Any] = field(default_factory=dict)
    redacted_command: list[str] = field(default_factory=list)
    unsupported_error: str | None = None
    provider_config_plan: dict[str, Any] = field(default_factory=dict)

    @property
    def is_supported(self) -> bool:
        return self.unsupported_error is None

    def write_provider_config_plan(self, run_dir: Path) -> Path:
        """Write provider_config_plan.json into the run directory."""
        plan_path = run_dir / "provider_config_plan.json"
        plan = {
            "schema_version": "provider_config_plan.v1",
            "worker_type": self.worker_type,
            "auth_mode": self.auth_mode,
            "provider_id": self.provider_config_plan.get("provider_id"),
            "model": self.provider_config_plan.get("model"),
            "base_url": self.provider_config_plan.get("base_url"),
            "api_protocol": self.provider_config_plan.get("api_protocol"),
            "credential_ref": self.provider_config_plan.get("credential_ref"),
            "credential_injected": self.provider_config_plan.get("credential_injected", False),
            "config_files": list(self.config_file_paths),
            "environment_keys": sorted(self.environment_overlay.keys()),
            "host_auth_path": self.provider_config_plan.get("host_auth_path"),
            "redacted": True,
        }
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return plan_path


class ProviderRenderer:
    """Base class for provider-specific command/config renderers."""

    worker_type: str = ""

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
        raise NotImplementedError

    def render_unsupported(self, *, auth_mode: str, error: str) -> ProviderRenderResult:
        return ProviderRenderResult(
            worker_type=self.worker_type,
            auth_mode=auth_mode,
            command_argv=[],
            unsupported_error=error,
        )

    @staticmethod
    def redact_text(value: str) -> str:
        """Redact sensitive tokens from a text value."""
        result = SENSITIVE_VALUE_RE.sub("[REDACTED]", value)
        return result

    @staticmethod
    def redact_command(command: list[str]) -> list[str]:
        """Redact sensitive values from a command argv list."""
        redacted: list[str] = []
        redact_next = False
        for token in command:
            if redact_next:
                redacted.append("[REDACTED]")
                redact_next = False
                continue
            lowered = token.lower()
            if SENSITIVE_KEY_RE.search(lowered) and "=" not in token:
                redacted.append(token)
                redact_next = True
                continue
            if "=" in token:
                key, _value = token.split("=", 1)
                if SENSITIVE_KEY_RE.search(key):
                    redacted.append(f"{key}=[REDACTED]")
                    continue
            redacted.append(SENSITIVE_VALUE_RE.sub("[REDACTED]", token))
        return redacted

    @staticmethod
    def redact_environment(env: dict[str, str]) -> dict[str, str]:
        """Redact sensitive values from environment overlay."""
        result: dict[str, str] = {}
        for key, value in env.items():
            if SENSITIVE_KEY_RE.search(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = SENSITIVE_VALUE_RE.sub("[REDACTED]", value)
        return result


class RendererRegistry:
    """Registry of provider renderers by worker_type."""

    def __init__(self) -> None:
        self._renderers: dict[str, ProviderRenderer] = {}

    def register(self, renderer: ProviderRenderer) -> None:
        self._renderers[renderer.worker_type] = renderer

    def get(self, worker_type: str) -> ProviderRenderer | None:
        return self._renderers.get(worker_type)

    def list_worker_types(self) -> list[str]:
        return sorted(self._renderers.keys())


_REGISTRY: RendererRegistry | None = None


def get_renderer_registry() -> RendererRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        from app.workers.provider_renderers import build_default_registry
        _REGISTRY = build_default_registry()
    return _REGISTRY
