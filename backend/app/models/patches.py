from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PatchType = Literal[
    "add_module",
    "add_module_group",
    "update_card",
    "review_run",
    "semantic_rollback",
]


class PatchOp(BaseModel):
    op: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GraphPatch(BaseModel):
    patch_id: str
    patch_type: PatchType
    source: str
    reason: str
    requires_user_confirmation: bool = True
    ops: list[PatchOp]


class Proposal(BaseModel):
    proposal_id: str
    patch_id: str
    title: str
    summary: str
    impact_summary: str
    status: str
    consistency_warnings: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ApplyResult(BaseModel):
    project_id: str
    patch_id: str
    commit_hash: str | None = None
    warnings: list[str] = Field(default_factory=list)
