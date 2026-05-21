from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutorReference(BaseModel):
    type: Literal["file", "directory", "url", "template"] = "file"
    path: str
    description: str | None = None


class ExecutorToolPolicy(BaseModel):
    network: str = "prompt"
    python: bool = True
    rscript: bool = False
    shell: bool = True
    git_write: bool = False


class RuntimeBindings(BaseModel):
    conda_env: str | None = None
    container_image: str | None = None
    working_dir: str = "."
    env: dict[str, str] = Field(default_factory=dict)


class ExecutorContext(BaseModel):
    executor_profile: str | None = None
    skills: list[str] = Field(default_factory=list)
    instruction_blocks: list[str] = Field(default_factory=list)
    references: list[ExecutorReference] = Field(default_factory=list)
    tool_policy: ExecutorToolPolicy = Field(default_factory=ExecutorToolPolicy)
    runtime_bindings: RuntimeBindings = Field(default_factory=RuntimeBindings)


class ManagerReportingContract(BaseModel):
    transport: Literal["stdout_bp_event", "file_jsonl"] = "stdout_bp_event"
    stdout_prefix: str = "BP_EVENT "
    file_path: str | None = None
    message_types: list[str] = Field(default_factory=lambda: ["progress_update", "issue_report", "final_report"])


class ExecutorStructuredEvent(BaseModel):
    type: Literal["progress_update", "issue_report", "final_report"]
    stage: str | None = None
    progress: int | None = None
    message: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] | None = None
    needs_manager: bool = False
    suggested_actions: list[str] = Field(default_factory=list)
    summary: str | None = None
    key_findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
