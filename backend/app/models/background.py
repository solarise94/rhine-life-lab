from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


BackgroundTaskType = Literal["card_run", "runtime_dependency_install", "batch_card_start", "cleanup"]
BackgroundTaskStatus = Literal["queued", "launching", "running", "waiting", "succeeded", "failed", "cancelled", "interrupted"]
WorkboardLane = Literal["running", "todo", "needs_manager", "completed", "ready_to_start", "blocked_for_user", "deferred"]
WorkboardConsumptionStatus = Literal["pending", "claimed", "processing", "done", "deferred", "failed", "blocked_for_user"]


class BackgroundTaskAffected(BaseModel):
    card_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)


class BackgroundTaskAdapter(BaseModel):
    kind: str
    session_id: str | None = None
    process_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackgroundTaskRecord(BaseModel):
    task_id: str
    task_type: BackgroundTaskType
    project_id: str
    status: BackgroundTaskStatus = "queued"
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    affected: BackgroundTaskAffected = Field(default_factory=BackgroundTaskAffected)
    adapter: BackgroundTaskAdapter
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WorkboardItemRecord(BaseModel):
    item_id: str
    lane: WorkboardLane
    kind: str
    title: str | None = None
    card_id: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    task_id: str | None = None
    source_item_id: str | None = None
    source_lane: WorkboardLane | None = None
    status: WorkboardConsumptionStatus = "pending"
    action_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    recommended_action: str | None = None
    summary: str | None = None
    message: str | None = None
    claimed_by_session_id: str | None = None
    claimed_at: str | None = None
    claim_expires_at: str | None = None
    updated_at: str | None = None


class BackgroundWorkboardState(BaseModel):
    items: dict[str, WorkboardItemRecord] = Field(default_factory=dict)
    last_revision: int = 0


class BackgroundWorkboardView(BaseModel):
    project_id: str
    revision: int
    counts: dict[str, int] = Field(default_factory=dict)
    running: list[dict[str, Any]] = Field(default_factory=list)
    todo: list[dict[str, Any]] = Field(default_factory=list)
    needs_manager: list[dict[str, Any]] = Field(default_factory=list)
    completed: list[dict[str, Any]] = Field(default_factory=list)
    ready_to_start: list[dict[str, Any]] = Field(default_factory=list)
    blocked_for_user: list[dict[str, Any]] = Field(default_factory=list)
    deferred: list[dict[str, Any]] = Field(default_factory=list)

