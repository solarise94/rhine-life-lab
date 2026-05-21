from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.executor import ExecutorContext


CardStatus = Literal[
    "proposed",
    "planned",
    "running",
    "reviewing",
    "needs_review",
    "accepted",
    "rejected",
    "stale",
    "superseded",
    "cancelled",
    "failed",
]
AggregateStatus = Literal[
    "all_accepted",
    "has_running",
    "has_failed",
    "partially_planned",
    "mixed",
    "stale",
]
CardType = Literal["module", "module_group", "run", "result"]


class CardAssetRef(BaseModel):
    label: str
    asset_id: str | None = None
    status: str | None = None


class TechnicalRefs(BaseModel):
    graph_nodes: list[str] = Field(default_factory=list)
    patches: list[str] = Field(default_factory=list)


class Card(BaseModel):
    card_id: str
    card_type: CardType
    title: str
    status: CardStatus
    step: int | None = None
    aggregate_status: AggregateStatus | None = None
    summary: str
    why: str = ""
    inputs: list[CardAssetRef] = Field(default_factory=list)
    outputs: list[CardAssetRef] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    manager_review: str = ""
    next_actions: list[str] = Field(default_factory=list)
    linked_modules: list[str] = Field(default_factory=list)
    linked_runs: list[str] = Field(default_factory=list)
    linked_assets: list[str] = Field(default_factory=list)
    technical_refs: TechnicalRefs = Field(default_factory=TechnicalRefs)
    progress_note: str | None = None
    executor_context: ExecutorContext | None = None
