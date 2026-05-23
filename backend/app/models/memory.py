from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ProjectMemoryKind = Literal["user_preference", "correction_memory"]


class ProjectMemoryItem(BaseModel):
    memory_id: str
    kind: ProjectMemoryKind
    summary: str
    source: str = "manager_chat"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    created_at: str
    updated_at: str

