from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


AUTH_MODE_CLI_NATIVE = "cli_native"
AUTH_MODE_PROJECT_API = "project_api"

AuthMode = Literal["cli_native", "project_api"]

WorkerType = Literal["pi", "opencode", "claude_code", "codex"]

SUPPORTED_AUTH_MODES: dict[str, set[str]] = {
    "pi": {AUTH_MODE_PROJECT_API},
    "opencode": {AUTH_MODE_CLI_NATIVE, AUTH_MODE_PROJECT_API},
    "claude_code": {AUTH_MODE_CLI_NATIVE},
    "codex": {AUTH_MODE_CLI_NATIVE},
}

SUPPORTED_API_PROTOCOLS: dict[str, set[str]] = {
    "pi": {"deepseek_compatible"},
    "opencode": {"openai_compatible", "provider_native"},
    "claude_code": set(),
    "codex": set(),
}


class ExecutorProfileSpec(BaseModel):
    """A configured executor profile that describes how to launch a CLI."""

    profile_id: str
    display_name: str
    worker_type: WorkerType
    auth_mode: AuthMode
    enabled: bool = True
    command: str | None = None
    api_protocol: str | None = None
    provider_id: str | None = None
    model: str | None = None
    base_url: str | None = None
    credential_ref: str | None = None
    permission_preset: str = "workspace_write"
    native_auth_readonly: bool = True

    @field_validator("auth_mode")
    @classmethod
    def _validate_auth_mode_for_worker(cls, value: str, info) -> str:
        worker_type = info.data.get("worker_type")
        if worker_type and worker_type in SUPPORTED_AUTH_MODES:
            allowed = SUPPORTED_AUTH_MODES[worker_type]
            if value not in allowed:
                raise ValueError(
                    f"auth_mode={value} is not supported for worker_type={worker_type}. "
                    f"Supported modes: {sorted(allowed)}"
                )
        return value

    @field_validator("api_protocol")
    @classmethod
    def _validate_api_protocol_for_worker(cls, value: str | None, info) -> str | None:
        if value is None:
            return value
        worker_type = info.data.get("worker_type")
        auth_mode = info.data.get("auth_mode")
        if auth_mode == AUTH_MODE_CLI_NATIVE:
            return value
        if worker_type and worker_type in SUPPORTED_API_PROTOCOLS:
            allowed = SUPPORTED_API_PROTOCOLS[worker_type]
            if allowed and value not in allowed:
                raise ValueError(
                    f"api_protocol={value} is not supported for worker_type={worker_type}. "
                    f"Supported protocols: {sorted(allowed)}"
                )
        return value


class ExecutorProfileValidationResult(BaseModel):
    """Result of validating an executor profile configuration."""

    profile_id: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cli_available: bool | None = None
    auth_configured: bool | None = None
    provider_configured: bool | None = None


def validate_profile(spec: ExecutorProfileSpec, *, settings: object | None = None) -> ExecutorProfileValidationResult:
    """Validate an executor profile spec against the current settings."""
    errors: list[str] = []
    warnings: list[str] = []
    cli_available: bool | None = None
    auth_configured: bool | None = None
    provider_configured: bool | None = None

    worker_type = spec.worker_type
    auth_mode = spec.auth_mode

    if not spec.enabled:
        return ExecutorProfileValidationResult(
            profile_id=spec.profile_id,
            valid=True,
            warnings=["Profile is disabled."],
        )

    if worker_type not in SUPPORTED_AUTH_MODES:
        errors.append(f"Unknown worker_type: {worker_type}")
        return ExecutorProfileValidationResult(
            profile_id=spec.profile_id,
            valid=False,
            errors=errors,
        )

    if auth_mode not in SUPPORTED_AUTH_MODES[worker_type]:
        errors.append(
            f"auth_mode={auth_mode} is not supported for worker_type={worker_type}. "
            f"Supported: {sorted(SUPPORTED_AUTH_MODES[worker_type])}"
        )

    if auth_mode == AUTH_MODE_PROJECT_API:
        if worker_type == "codex":
            errors.append("Codex project API mode is not implemented yet. Use cli_native instead.")
        elif worker_type == "claude_code":
            errors.append("Claude Code project API mode is not supported. Use cli_native with local Claude login.")
        elif worker_type == "opencode":
            if spec.api_protocol not in {"openai_compatible", "provider_native"}:
                errors.append(
                    f"OpenCode project API mode requires api_protocol in (openai_compatible, provider_native), got {spec.api_protocol}."
                )
            if not spec.credential_ref:
                warnings.append("OpenCode project API mode will default to credential_ref=project:openai_api_key.")
                auth_configured = True
            else:
                auth_configured = True
        elif worker_type == "pi":
            auth_configured = True

    if auth_mode == AUTH_MODE_CLI_NATIVE:
        auth_configured = True

    if settings is not None:
        command_setting = _resolve_command_setting(worker_type, settings)
        if command_setting:
            cli_available = True
        else:
            cli_available = False
            warnings.append(f"No command template configured for {worker_type}. Set the corresponding settings field.")

    return ExecutorProfileValidationResult(
        profile_id=spec.profile_id,
        valid=not errors,
        errors=errors,
        warnings=warnings,
        cli_available=cli_available,
        auth_configured=auth_configured,
        provider_configured=provider_configured,
    )


def _resolve_command_setting(worker_type: str, settings: object) -> str | None:
    setting_name = f"{worker_type}_command"
    return getattr(settings, setting_name, None)


def default_profiles() -> list[ExecutorProfileSpec]:
    """Return the default executor profiles."""
    return [
        ExecutorProfileSpec(
            profile_id="pi-project-api",
            display_name="Pi Agent (project API)",
            worker_type="pi",
            auth_mode=AUTH_MODE_PROJECT_API,
            api_protocol="deepseek_compatible",
            provider_id="deepseek",
        ),
        ExecutorProfileSpec(
            profile_id="opencode-cli-native",
            display_name="OpenCode (local CLI login)",
            worker_type="opencode",
            auth_mode=AUTH_MODE_CLI_NATIVE,
            native_auth_readonly=True,
        ),
        ExecutorProfileSpec(
            profile_id="opencode-project-api",
            display_name="OpenCode (project API)",
            worker_type="opencode",
            auth_mode=AUTH_MODE_PROJECT_API,
            api_protocol="openai_compatible",
            native_auth_readonly=False,
        ),
        ExecutorProfileSpec(
            profile_id="claude-code-cli-native",
            display_name="Claude Code (local CLI login)",
            worker_type="claude_code",
            auth_mode=AUTH_MODE_CLI_NATIVE,
            native_auth_readonly=True,
        ),
        ExecutorProfileSpec(
            profile_id="codex-cli-native",
            display_name="Codex (local CLI login)",
            worker_type="codex",
            auth_mode=AUTH_MODE_CLI_NATIVE,
            native_auth_readonly=True,
        ),
    ]
