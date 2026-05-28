from __future__ import annotations

import json
from http import client
import time
from typing import Any, Literal
from urllib import error


ProviderErrorCategory = Literal[
    "auth_error",
    "rate_limited",
    "provider_unavailable",
    "timeout",
    "network_error",
    "invalid_response",
    "bad_request",
    "unknown",
]


class ProviderAPIError(RuntimeError):
    def __init__(
        self,
        *,
        provider: str,
        role: str,
        category: ProviderErrorCategory,
        message: str,
        status_code: int | None = None,
        error_code: str | None = None,
        detail: str | None = None,
        retryable: bool | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.role = role
        self.category = category
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail
        self.retryable = is_retryable_category(category) if retryable is None else retryable
        self.attempt = attempt
        self.max_attempts = max_attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "role": self.role,
            "category": self.category,
            "message": self.message,
            "status_code": self.status_code,
            "error_code": self.error_code,
            "detail": self.detail,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
        }

    def __str__(self) -> str:
        status = f" HTTP {self.status_code}" if self.status_code is not None else ""
        code = f" ({self.error_code})" if self.error_code else ""
        return f"{self.provider} {self.role} API error{status}{code}: {self.message}"


def provider_error_from_http_error(
    exc: error.HTTPError,
    *,
    provider: str,
    role: str,
    configured_model: str | None = None,
    resolved_model: str | None = None,
) -> ProviderAPIError:
    detail = exc.read().decode("utf-8", errors="replace")
    return provider_error_from_http_status_detail(
        exc.code,
        detail,
        provider=provider,
        role=role,
        configured_model=configured_model,
        resolved_model=resolved_model,
    )


def provider_error_from_http_status_detail(
    status_code: int,
    detail: str,
    *,
    provider: str,
    role: str,
    configured_model: str | None = None,
    resolved_model: str | None = None,
) -> ProviderAPIError:
    error_code, message = _extract_provider_error(detail)
    message = message or detail or f"HTTP {status_code}"
    guidance = _tool_model_guidance(message, configured_model, resolved_model)
    if guidance:
        message = f"{message}{guidance}"
    return ProviderAPIError(
        provider=provider,
        role=role,
        category=category_for_http_status(status_code),
        status_code=status_code,
        error_code=error_code,
        detail=detail,
        message=message,
    )


def provider_error_from_url_error(exc: error.URLError, *, provider: str, role: str) -> ProviderAPIError:
    reason = getattr(exc, "reason", exc)
    category: ProviderErrorCategory = "timeout" if isinstance(reason, TimeoutError) else "network_error"
    return ProviderAPIError(
        provider=provider,
        role=role,
        category=category,
        message=str(reason),
        detail=str(exc),
    )


def provider_error_from_exception(exc: BaseException, *, provider: str, role: str) -> ProviderAPIError:
    category: ProviderErrorCategory = "timeout" if isinstance(exc, TimeoutError) else "network_error"
    if isinstance(exc, client.HTTPException):
        category = "network_error"
    return ProviderAPIError(
        provider=provider,
        role=role,
        category=category,
        message=str(exc),
        detail=repr(exc),
    )


def provider_invalid_response_error(
    exc: BaseException,
    *,
    provider: str,
    role: str,
    message: str,
    detail: str | None = None,
) -> ProviderAPIError:
    return ProviderAPIError(
        provider=provider,
        role=role,
        category="invalid_response",
        message=message,
        detail=detail or str(exc),
        retryable=False,
    )


def category_for_http_status(status_code: int) -> ProviderErrorCategory:
    if status_code in {401, 403}:
        return "auth_error"
    if status_code == 429:
        return "rate_limited"
    if status_code in {500, 502, 503, 504}:
        return "provider_unavailable"
    if status_code == 408:
        return "timeout"
    if 400 <= status_code < 500:
        return "bad_request"
    return "unknown"


def is_retryable_category(category: str) -> bool:
    return category in {"rate_limited", "provider_unavailable", "timeout", "network_error"}


def provider_error_message(err: ProviderAPIError) -> str:
    status = f" HTTP {err.status_code}" if err.status_code is not None else ""
    retry = "retryable" if err.retryable else "not retryable"
    return f"{err.provider} {err.role} request failed{status}: {err.message} [{err.category}, {retry}]"


def retry_provider_call(
    operation,
    *,
    max_attempts: int = 5,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 16.0,
):
    attempts = max(1, max_attempts)
    last_error: ProviderAPIError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except ProviderAPIError as exc:
            exc.attempt = attempt
            exc.max_attempts = attempts
            last_error = exc
            if not exc.retryable or attempt >= attempts:
                raise
            time.sleep(min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1))))
    if last_error is not None:
        raise last_error
    return operation()


def _extract_provider_error(detail: str) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    error_payload = payload.get("error")
    if isinstance(error_payload, dict):
        code = error_payload.get("code")
        message = error_payload.get("message")
        return (
            str(code) if code is not None else None,
            str(message) if message is not None else None,
        )
    return None, None


def _tool_model_guidance(message: str, configured_model: str | None, resolved_model: str | None) -> str:
    guidance = ""
    if "does not support this tool_choice" in message:
        guidance = (
            " Manager tool-use requests require a DeepSeek v4 model such as "
            "`deepseek-v4-pro` or `deepseek-v4-flash`."
        )
    if configured_model and resolved_model and configured_model != resolved_model:
        guidance += f" Configured model `{configured_model}` was normalized to `{resolved_model}`."
    return guidance
