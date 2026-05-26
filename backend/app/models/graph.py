from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.cards import CardStatus


ModuleType = Literal["analysis_module", "module_group"]
AssetStatus = Literal["candidate", "valid", "stale", "superseded", "rejected", "archived", "missing"]
RunStatus = Literal["queued", "launching", "running", "reviewing", "needs_approval", "success", "failed", "cancelled", "reviewed"]
ClaimStatus = Literal["candidate", "valid", "stale", "superseded", "rejected", "archived", "missing"]


class ModuleRef(BaseModel):
    module_id: str
    title: str
    status: CardStatus


class Module(BaseModel):
    module_id: str
    title: str
    type: ModuleType
    status: CardStatus
    summary: str
    depends_on_assets: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    linked_cards: list[str] = Field(default_factory=list)
    linked_runs: list[str] = Field(default_factory=list)
    submodules: list[ModuleRef] = Field(default_factory=list)
    created_by: str
    created_at: str


class Asset(BaseModel):
    asset_id: str
    asset_type: str
    title: str
    status: AssetStatus
    created_by_run: str | None = None
    path: str
    artifact_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    report_selected: bool = False


class Claim(BaseModel):
    claim_id: str
    text: str
    status: ClaimStatus
    depends_on_assets: list[str] = Field(default_factory=list)
    created_by_run: str | None = None
    report_selected: bool = False


class RunRecord(BaseModel):
    run_id: str
    card_id: str
    module_id: str | None = None
    status: RunStatus
    title: str
    summary: str
    started_at: str
    finished_at: str | None = None
    worker_type: str = "pi"
    cancel_reason: str | None = None
    archived_at: str | None = None
    cleanup_status: Literal["pending", "completed"] | None = None
    needs_manager_attention: bool = False


class ReportItem(BaseModel):
    item_id: str
    section: str
    title: str
    summary: str
    linked_asset_ids: list[str] = Field(default_factory=list)
    linked_claim_ids: list[str] = Field(default_factory=list)


class GraphState(BaseModel):
    modules: list[Module] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    runs: list[RunRecord] = Field(default_factory=list)
    report_items: list[ReportItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
