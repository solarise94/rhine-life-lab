from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.executor import (
    ExecutorContext,
    ExecutorScriptAssetBinding,
    ExecutorScriptAssetRequirement,
)
from app.models.output_contracts import ArtifactClass


class TemplateIoBinding(BaseModel):
    label: str
    role: str | None = None
    artifact_class: ArtifactClass | None = None
    accepted_formats: list[str] = Field(default_factory=list)
    preferred_format: str | None = None
    asset_id: str | None = None
    status: str | None = None
    required: bool = True
    description: str | None = None


class TemplateBundleFile(BaseModel):
    original_path: str
    stored_path: str
    description: str | None = None


class TemplateBundle(BaseModel):
    files: list[TemplateBundleFile] = Field(default_factory=list)
    relative_dependency_graph: list[dict[str, Any]] = Field(default_factory=list)
    path_rewrites: dict[str, str] = Field(default_factory=dict)
    parameter_bindings: dict[str, Any] = Field(default_factory=dict)
    script_asset_requirements: list[ExecutorScriptAssetRequirement] = Field(default_factory=list)
    script_asset_bindings: list[ExecutorScriptAssetBinding] = Field(default_factory=list)


class TemplateSpec(BaseModel):
    card_title_pattern: str
    summary_template: str
    why_template: str = ""
    inputs_schema: list[TemplateIoBinding] = Field(default_factory=list)
    outputs_schema: list[TemplateIoBinding] = Field(default_factory=list)
    executor_context: ExecutorContext = Field(default_factory=ExecutorContext)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_bindings: dict[str, Any] = Field(default_factory=dict)
    instruction_blocks: list[str] = Field(default_factory=list)
    prompt_blocks: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    success_signals: list[str] = Field(default_factory=list)
    failure_signals: list[str] = Field(default_factory=list)
    bundle: TemplateBundle = Field(default_factory=TemplateBundle)


class CardTemplate(BaseModel):
    template_id: str
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    domain: str = "bioinformatics"
    card_type: str = "module"
    source_card_type: str = "module"
    created_at: str
    updated_at: str
    last_verified_at: str | None = None
    reuse_count: int = 0
    confidence_score: float = 0.5
    status: str = "active"
    source_card_id: str | None = None
    source_project_id: str | None = None
    spec: TemplateSpec
