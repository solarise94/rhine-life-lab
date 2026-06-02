from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# Doc 42: 5-state episode model (replaces legacy 8-state continuous/once model)
ManagerAutoStateValue = Literal["running", "idle", "pending_wake", "complete", "finished"]
ManagerAutoDirectiveStatus = Literal["pending", "consumed", "superseded", "rejected"]


class ManagerAutoDirective(BaseModel):
    id: str
    message_id: str | None = None
    text: str
    created_at: str
    status: ManagerAutoDirectiveStatus = "pending"
    resolved_at: str | None = None
    resolution_note: str | None = None


class ManagerAutoState(BaseModel):
    enabled: bool = False
    owner_session_id: str | None = None
    state: ManagerAutoStateValue = "idle"
    started_at: str | None = None
    last_wake_id: str | None = None
    chain_count: int = 0
    max_chain_count: int = 50
    active_run_id: str | None = None
    active_job_id: str | None = None
    auto_scope_id: str | None = None
    stopped_at: str | None = None
    stop_reason: str | None = None
    stop_message: str | None = None
    pending_directives: list[ManagerAutoDirective] = Field(default_factory=list)
    scope_objective: str | None = None
    # Doc 42 latch fields (Section 7)
    fuel_revision: int = 0
    last_notified_revision: int = 0
    wake_in_flight: bool = False
    completion_notified: bool = False
    # Doc 42 risk-control fields (Section 7)
    wake_window: list[str] = Field(default_factory=list)
    finished_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # 1. Map legacy state values to doc-42 states
        old_state = data.get("state")
        state_map = {
            "active": "pending_wake",
            "thinking": "running",
            "blocked": "idle",
            "completed": "complete",
            "stopped": "finished",
            "cancelled": "finished",
        }
        if old_state in state_map:
            data["state"] = state_map[old_state]

        # 2. If finished and no finished_at, derive from stopped_at
        if data.get("state") == "finished" and not data.get("finished_at"):
            data["finished_at"] = data.get("stopped_at")

        # 3. Drop doc-41 / legacy fields that are not in doc-42 contract
        for field in (
            "mode",
            "view_workboard",
            "consume_workboard",
            "wake_allowed",
            "last_signaled_board_revision",
            "last_signaled_workboard_fingerprint",
            "last_signaled_workboard_fingerprint_at",
            "chain_limit_basis",
            "expires_at",
        ):
            data.pop(field, None)

        # 4. Ensure doc-42 latch fields have defaults
        data.setdefault("fuel_revision", 0)
        data.setdefault("last_notified_revision", 0)
        data.setdefault("wake_in_flight", False)
        data.setdefault("completion_notified", False)
        data.setdefault("wake_window", [])
        data.setdefault("finished_at", None)

        # 5. Bump max_chain_count to doc-42 fixed default (50)
        if data.get("max_chain_count", 0) < 50:
            data["max_chain_count"] = 50

        return data
