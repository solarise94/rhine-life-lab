from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.executor import ExecutorContext, ManagerReportingContract


ManifestStatus = Literal["success", "failed", "partial"]


class TaskPacketAsset(BaseModel):
    asset_id: str
    path: str
    type: str
    title: str | None = None
    status: str | None = None


class ExpectedOutput(BaseModel):
    role: str
    type: str
    path_hint: str
    label: str | None = None
    asset_id: str | None = None


class TaskPacketCardInput(BaseModel):
    label: str
    asset_id: str | None = None
    asset_path: str | None = None
    asset_type: str | None = None
    status: str | None = None


class TaskPacketCardOutput(BaseModel):
    label: str
    asset_id: str | None = None
    status: str | None = None
    role: str
    type: str
    path_hint: str


class RunContext(BaseModel):
    run_id: str
    worker_type: str
    project_root: str
    run_dir: str
    result_dir: str


class ExecutionPolicy(BaseModel):
    mode: Literal["audit", "guarded", "strict"] = "audit"
    network: str = "prompt"
    write_policy: str = "allowed_paths_with_post_run_audit"
    on_policy_violation: str = "fail_or_quarantine"


class TaskPacket(BaseModel):
    task_id: str
    project_id: str
    card_id: str
    card_title: str | None = None
    card_status: str | None = None
    goal: str
    input_assets: list[TaskPacketAsset] = Field(default_factory=list)
    card_inputs: list[TaskPacketCardInput] = Field(default_factory=list)
    card_outputs: list[TaskPacketCardOutput] = Field(default_factory=list)
    expected_outputs: list[ExpectedOutput] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    readonly_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    constraints: list[str] = Field(default_factory=list)
    worker_instructions: str
    run_context: RunContext | None = None
    executor_context: ExecutorContext | None = None
    manager_reporting_contract: ManagerReportingContract | None = None


class CreatedAsset(BaseModel):
    role: str
    type: str
    path: str
    label: str | None = None
    asset_id: str | None = None
    description: str | None = None


class CodeArtifact(BaseModel):
    path: str
    language: str | None = None
    purpose: str | None = None
    sha256: str | None = None


class ValidationIssue(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    path: str | None = None
    repair_hint: str | None = None


class ExecutorValidationReport(BaseModel):
    status: Literal["pass", "warn", "fail"]
    summary: str
    issues: list[ValidationIssue] = Field(default_factory=list)
    reviewer: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    run_id: str
    status: ManifestStatus
    summary: str
    inputs_used: list[TaskPacketAsset] = Field(default_factory=list)
    created_assets: list[CreatedAsset] = Field(default_factory=list)
    code_artifacts: list[CodeArtifact] = Field(default_factory=list)
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
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
    code_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    commands_executed: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    key_findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    executor_validation: ExecutorValidationReport | None = None


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
    payload: dict[str, Any] = Field(default_factory=dict)


class ManagerReview(BaseModel):
    run_id: str
    decision: Literal["accept", "reject"]
    summary: str
    accepted_assets: list[str] = Field(default_factory=list)
    new_claims: list[dict[str, Any]] = Field(default_factory=list)
    card_updates: list[dict[str, Any]] = Field(default_factory=list)
    downstream_effects: list[dict[str, Any]] = Field(default_factory=list)
    needs_user_attention: bool = False
