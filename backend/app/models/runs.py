from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.executor import ExecutorContext, ManagerReportingContract
from app.models.output_contracts import CreatedAssetRecord, TaskOutputSpec


ManifestStatus = Literal["success", "failed", "partial"]
FailureReasonCode = Literal[
    "runtime_dependency_missing",
    "input_missing",
    "input_invalid",
    "permission_denied",
    "tool_unavailable",
    "execution_error",
    "contract_violation",
    "unknown",
]


class TaskPacketAsset(BaseModel):
    asset_id: str
    path: str
    type: str
    title: str | None = None
    status: str | None = None
    requested_asset_id: str | None = None
    resolved_by: str | None = None
    producer_card_id: str | None = None
    producer_role: str | None = None


class TaskPacketCardInput(BaseModel):
    label: str
    asset_id: str | None = None
    requested_asset_id: str | None = None
    resolved_asset_id: str | None = None
    resolved_by: str | None = None
    producer_card_id: str | None = None
    producer_role: str | None = None
    asset_path: str | None = None
    asset_type: str | None = None
    status: str | None = None


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


ExpectedOutput = TaskOutputSpec
TaskPacketCardOutput = TaskOutputSpec
CreatedAsset = CreatedAssetRecord


class TaskPacket(BaseModel):
    task_id: str
    project_id: str
    card_id: str
    card_title: str | None = None
    card_status: str | None = None
    goal: str
    input_assets: list[TaskPacketAsset] = Field(default_factory=list)
    card_inputs: list[TaskPacketCardInput] = Field(default_factory=list)
    card_outputs: list[TaskOutputSpec] = Field(default_factory=list)
    expected_outputs: list[TaskOutputSpec] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    readonly_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    constraints: list[str] = Field(default_factory=list)
    worker_instructions: str
    run_context: RunContext | None = None
    executor_context: ExecutorContext | None = None
    manager_reporting_contract: ManagerReportingContract | None = None


class CodeArtifact(BaseModel):
    path: str
    language: str | None = None
    purpose: str | None = None
    sha256: str | None = None


class ManagerReport(BaseModel):
    summary: str | None = None
    warnings: list[str] = Field(default_factory=list)


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
    schema_version: str | None = None
    run_id: str
    status: ManifestStatus
    summary: str
    inputs_used: list[TaskPacketAsset] = Field(default_factory=list)
    created_assets: list[CreatedAssetRecord] = Field(default_factory=list)
    code_artifacts: list[CodeArtifact] = Field(default_factory=list)
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
    commands_executed: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    key_findings: list[str] = Field(default_factory=list)
    recommended_graph_updates: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    manager_report: ManagerReport | None = None


class ExecutorManifestV2(BaseModel):
    schema_version: Literal["executor_manifest.v2"]
    summary: str
    created_assets: list[CreatedAssetRecord] = Field(default_factory=list)
    code_artifacts: list[CodeArtifact] = Field(default_factory=list)
    manager_report: ManagerReport = Field(default_factory=ManagerReport)


class ExecutorCompletionReport(BaseModel):
    schema_version: Literal["executor_completion.v1"]
    candidate_manifest_path: str
    canonical_manifest: dict[str, Any] | None = None


class ExecutorFailureReport(BaseModel):
    schema_version: Literal["executor_failure.v1"]
    reason_code: FailureReasonCode = "unknown"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class TerminalReport(BaseModel):
    schema_version: Literal["executor_terminal_report.v1"]
    run_id: str
    terminal_kind: Literal["report_complete", "report_fail", "synthetic_failure"]
    accepted_at: str
    summary: str
    reason_code: FailureReasonCode | None = None
    status: Literal["pending_review", "failed"] = "failed"
    completion_report_path: str | None = None
    failure_report_path: str | None = None
    candidate_manifest_path: str | None = None


class ExecutorResultState(BaseModel):
    schema_version: Literal["executor_result_state.v1"] = "executor_result_state.v1"
    report_complete_failure_count: int = 0
    last_validation_errors: list[str] = Field(default_factory=list)


class ManifestReviewContext(BaseModel):
    run_id: str
    summary: str
    status: ManifestStatus
    declared_input_assets: list[dict[str, Any]] = Field(default_factory=list)
    input_conclusion: str | None = None
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
