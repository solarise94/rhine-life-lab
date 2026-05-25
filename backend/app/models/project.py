from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ProjectStatus = Literal["active", "archived", "error"]


class ProjectRuntimePreferences(BaseModel):
    script_preference: Literal["auto", "prefer_python", "prefer_r", "prefer_mixed"] = "auto"
    python_runtime: str | None = None
    r_runtime: str | None = None


class ProjectState(BaseModel):
    project_id: str
    name: str
    status: ProjectStatus
    schema_version: str
    current_goal: str
    created_at: str
    updated_at: str
    runtime_preferences: ProjectRuntimePreferences = Field(default_factory=ProjectRuntimePreferences)


class ProjectSummary(ProjectState):
    card_counts: dict[str, int]
    result_counts: dict[str, int]
