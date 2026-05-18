from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ManifestStatus = Literal["success", "failed", "partial"]


class TaskPacketAsset(BaseModel):
    asset_id: str
    path: str
    type: str


class ExpectedOutput(BaseModel):
    role: str
    path_hint: str


class ExecutionPolicy(BaseModel):
    mode: Literal["audit", "guarded", "strict"] = "audit"
    network: str = "prompt"
    write_policy: str = "allowed_paths_with_post_run_audit"
    on_policy_violation: str = "fail_or_quarantine"


class TaskPacket(BaseModel):
    task_id: str
    project_id: str
    card_id: str
    goal: str
    input_assets: list[TaskPacketAsset] = Field(default_factory=list)
    expected_outputs: list[ExpectedOutput] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    readonly_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    constraints: list[str] = Field(default_factory=list)
    worker_instructions: str


class CreatedAsset(BaseModel):
    role: str
    type: str
    path: str
    description: str | None = None


class Manifest(BaseModel):
    run_id: str
    status: ManifestStatus
    summary: str
    inputs_used: list[TaskPacketAsset] = Field(default_factory=list)
    created_assets: list[CreatedAsset] = Field(default_factory=list)
    commands_executed: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    key_findings: list[str] = Field(default_factory=list)
    recommended_graph_updates: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ManifestReviewContext(BaseModel):
    run_id: str
    summary: str
    status: ManifestStatus
    created_assets: list[dict[str, Any]] = Field(default_factory=list)
    commands_executed: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    key_findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class RunEvent(BaseModel):
    event_id: str
    run_id: str
    card_id: str
    source: str
    event_type: str
    visibility: str
    preview_id: str
    utterance_id: str
    stream_state: str
    message: str
    created_at: str


class ManagerReview(BaseModel):
    run_id: str
    decision: Literal["accept", "reject"]
    summary: str
    accepted_assets: list[str] = Field(default_factory=list)
    new_claims: list[dict[str, Any]] = Field(default_factory=list)
    card_updates: list[dict[str, Any]] = Field(default_factory=list)
    downstream_effects: list[dict[str, Any]] = Field(default_factory=list)
    needs_user_attention: bool = False
