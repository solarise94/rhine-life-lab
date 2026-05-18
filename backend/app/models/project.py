from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


ProjectStatus = Literal["active", "archived", "error"]


class ProjectState(BaseModel):
    project_id: str
    name: str
    status: ProjectStatus
    schema_version: str
    current_goal: str
    created_at: str
    updated_at: str


class ProjectSummary(ProjectState):
    card_counts: dict[str, int]
    result_counts: dict[str, int]

