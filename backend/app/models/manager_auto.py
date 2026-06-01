from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ManagerAutoMode = Literal["continuous", "once"]
ManagerAutoStateValue = Literal["active", "idle", "running", "thinking", "blocked", "completed", "cancelled", "stopped"]
ManagerAutoStopReason = str
ManagerAutoDirectiveStatus = Literal["pending", "consumed", "superseded", "rejected"]
ManagerWakeStatus = Literal["queued", "running", "done", "failed", "skipped"]


class ManagerAutoDirective(BaseModel):
    id: str
    message_id: str | None = None
    text: str
    created_at: str
    status: ManagerAutoDirectiveStatus = "pending"
    resolved_at: str | None = None
    resolution_note: str | None = None


class ManagerAutoChainLimitBasis(BaseModel):
    executable_card_count: int = 0
    formula: str = "max(10, min(80, executable_card_count * 3))"


class ManagerAutoState(BaseModel):
    enabled: bool = False
    mode: ManagerAutoMode = "continuous"
    owner_session_id: str | None = None
    state: ManagerAutoStateValue = "idle"
    started_at: str | None = None
    last_wake_id: str | None = None
    chain_count: int = 0
    max_chain_count: int = 10
    chain_limit_basis: ManagerAutoChainLimitBasis = Field(default_factory=ManagerAutoChainLimitBasis)
    active_run_id: str | None = None
    active_job_id: str | None = None
    view_workboard: bool = False
    consume_workboard: bool = False
    last_signaled_board_revision: int | None = None
    last_signaled_workboard_fingerprint: str | None = None
    last_signaled_workboard_fingerprint_at: str | None = None
    auto_scope_id: str | None = None
    stopped_at: str | None = None
    stop_reason: ManagerAutoStopReason | None = None
    stop_message: str | None = None
    pending_directives: list[ManagerAutoDirective] = Field(default_factory=list)
    scope_objective: str | None = None
    wake_allowed: bool = False
    expires_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("enabled") is True and "wake_allowed" not in data:
                data["wake_allowed"] = True
        return data


class ManagerWakeSource(BaseModel):
    card_id: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    wake_id: str | None = None
    reason: str | None = None


class ManagerWakeEvent(BaseModel):
    wake_id: str
    project_id: str
    kind: str
    source_type: str
    source_id: str
    card_id: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    severity: str = "info"
    message: str
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    source: ManagerWakeSource | None = None
    idempotency_key: str
    status: ManagerWakeStatus = "queued"
    created_at: str
    processed_at: str | None = None
    claimed_at: str | None = None
    processor_id: str | None = None
    attempts: int = 0
    error: str | None = None
