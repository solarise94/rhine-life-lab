from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.models.card_templates import CardTemplate, TemplateBundle, TemplateBundleFile, TemplateIoBinding, TemplateSpec
from app.models.cards import Card, CardAssetRef
from app.models.executor import ExecutorContext, ExecutorReference, ExecutorScriptAssetBinding
from app.models.memory import ProjectMemoryItem
from app.models.output_contracts import CardOutputSpec
from app.models.runs import Manifest
from app.services.app_config_service import AppConfigService
from app.services.asset_timeline_service import AssetTimelineService
from app.services.dependency_attention_service import DependencyAttentionService
from app.services.library_registry_service import LibraryRegistryService
from app.services.manager_planner import ManagerPlanningError
from app.services.module_group_state_service import ModuleGroupStateService
from app.services.project_service import ProjectService
from app.services.result_asset_service import ResultAssetService
from app.services.runtime_dependency_job_service import RuntimeDependencyJobService
from app.services.utils import atomic_write_json, resolve_within, utc_now
from app.workers.command_worker import CommandTemplateWorkerAdapter
from app.services.worker_service import WorkerService


class ToolPolicyPayload(BaseModel):
    network: str | None = Field(default=None, pattern="^(allow|deny|prompt)$")
    python: bool | None = None
    rscript: bool | None = None
    shell: bool | None = None
    git_write: bool | None = None

    @field_validator("network", mode="before")
    @classmethod
    def normalize_network_policy(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool):
            return "allow" if value else "deny"
        text = str(value).strip().lower()
        if text == "true":
            return "allow"
        if text == "false":
            return "deny"
        return text


class RuntimeBindingsPayload(BaseModel):
    conda_env: str | None = None
    r_env: str | None = None
    working_dir: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class ConfigureCardExecutionPayload(BaseModel):
    card_id: str | None = None
    card_ids: list[str] = Field(default_factory=list)
    skills: list[str] | None = None
    mcp_servers: list[str] | None = None
    tool_policy: ToolPolicyPayload | None = None
    runtime_bindings: RuntimeBindingsPayload | None = None
    instruction_blocks: list[str] | None = None
    progress_note: str | None = None


class StartCardRunPayload(BaseModel):
    card_id: str
    worker_type: str | None = None
    profile_id: str | None = None
    python_runtime: str | None = None
    r_runtime: str | None = None


class StopCardRunPayload(BaseModel):
    run_id: str | None = None
    card_id: str | None = None
    reason: str | None = None


class ReviewCardRunPayload(BaseModel):
    run_id: str | None = None
    card_id: str | None = None
    accept: bool = True


class CleanupRunHistoryPayload(BaseModel):
    run_id: str | None = None
    card_id: str | None = None
    statuses: list[str] = Field(default_factory=list)
    keep_latest_per_card: bool = True
    include_valid_assets: bool = False
    dry_run: bool = False
    reason: str | None = None


class SearchCardTemplatesPayload(BaseModel):
    query: str = ""
    tags: list[str] = Field(default_factory=list)
    card_type: str | None = None
    limit: int = 5


class SaveCardTemplatePayload(BaseModel):
    card_id: str
    title: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)


class ScriptAssetBindingPayload(BaseModel):
    requirement_id: str
    asset_id: str


class InstantiateCardTemplatePayload(BaseModel):
    template_id: str
    card_id: str
    title: str | None = None
    step: int | None = None
    input_bindings: list[dict[str, Any]] = Field(default_factory=list)
    output_bindings: list[dict[str, Any]] = Field(default_factory=list)
    script_asset_bindings: list[ScriptAssetBindingPayload] = Field(default_factory=list)
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class InstallRuntimeDependenciesPayload(BaseModel):
    ecosystem: str
    runtime: str
    packages: list[str] = Field(default_factory=list)
    manager: str | None = None
    timeout_seconds: int = 600
    source: dict[str, Any] = Field(default_factory=dict)


class FindCardsPayload(BaseModel):
    query: str = ""
    status: str | None = None
    step: int | None = None
    asset_id: str | None = None
    limit: int = 12


class FindAssetsPayload(BaseModel):
    query: str = ""
    role: str | None = None
    artifact_class: str | None = None
    format: str | None = None
    producer_card_id: str | None = None
    status: str | None = None
    limit: int = 12


class InspectDependencyAttentionPayload(BaseModel):
    card_ids: list[str] = Field(default_factory=list)
    source_card_id: str | None = None
    include_recursive_downstream: bool = False
    max_issues: int = 50


class PlanCardWritePayload(BaseModel):
    action: str = "create"
    card_id: str | None = None
    card: dict[str, Any] = Field(default_factory=dict)


class ManagerBlueprintTools:
    """Controlled tools exposed to the external manager agent runtime."""

    def __init__(
        self,
        project_service: ProjectService,
        worker_service: WorkerService | None = None,
        runtime_dependency_job_service: RuntimeDependencyJobService | None = None,
        library_registry_service: LibraryRegistryService | None = None,
    ) -> None:
        self.project_service = project_service
        self.worker_service = worker_service
        self.runtime_dependency_job_service = runtime_dependency_job_service
        self.library_registry_service = library_registry_service or LibraryRegistryService(
            project_service,
            AppConfigService(project_service.settings),
            project_service.settings,
        )
        self.result_asset_service = ResultAssetService(project_service)
        self.asset_timeline_service = AssetTimelineService()
        self.dependency_attention_service = DependencyAttentionService()

    def get_project_context(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        project = snapshot["project"]
        graph = snapshot["graph"]
        return {
            "project": project.model_dump(),
            "cards": [card.model_dump() for card in snapshot["cards"]],
            "modules": [module.model_dump() for module in graph.modules],
            "assets": [asset.model_dump() for asset in graph.assets],
            "runs": [run.model_dump() for run in graph.runs],
            "claims": [claim.model_dump() for claim in graph.claims],
        }

    def inspect_project_summary(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        project = snapshot["project"]
        graph = snapshot["graph"]
        timeline = self.asset_timeline_service.build(project_id, snapshot)
        timeline_cards = {item["card_id"]: item for item in timeline["cards"]}
        runs_by_card = self._runs_by_card(graph.runs)
        cards = [
            self._compact_card(card, timeline_cards.get(card.card_id), runs_by_card.get(card.card_id, []))
            for card in snapshot["cards"]
        ]
        status_counts: dict[str, int] = {}
        for card in snapshot["cards"]:
            status_counts[card.status] = status_counts.get(card.status, 0) + 1
        materialized_assets = len(graph.assets)
        planned_assets = len([asset for asset in timeline["assets"] if asset.get("planned")])
        active_runs = [
            self._compact_run(run)
            for run in graph.runs
            if run.status in {"queued", "running", "reviewing", "needs_approval"}
        ]
        blockers = self._project_blockers(snapshot, timeline)
        attention = self.dependency_attention_service.analyze_project(snapshot)
        attention_by_card = attention["issues_by_card"]
        cards = [
            {
                **card,
                "dependency_attention_count": len(attention_by_card.get(card["card_id"], [])),
                "attention_severity": DependencyAttentionService.attention_severity(
                    attention_by_card.get(card["card_id"], [])
                ),
            }
            for card in cards
        ]
        return {
            "project_id": project_id,
            "project": {
                "name": project.name,
                "status": project.status,
                "current_goal": project.current_goal,
                "runtime_preferences": project.runtime_preferences.model_dump(),
            },
            "cards": cards,
            "counts": {
                "cards": len(cards),
                "card_statuses": status_counts,
                "materialized_assets": materialized_assets,
                "planned_assets": planned_assets,
                "active_runs": len(active_runs),
                "blockers": len(blockers),
                "dependency_attention": attention["issue_count"],
            },
            "active_runs": active_runs[:8],
            "blockers": blockers[:12],
            "dependency_attention": attention["issues"][:12],
            "dependency_attention_count": attention["issue_count"],
            "dependency_attention_fingerprint": attention["fingerprint"],
            "timeline": {
                "parallel_batches": timeline["parallel_batches"],
                "cycle_card_ids": timeline["cycle_card_ids"],
                "duplicate_output_assets": timeline["duplicate_output_assets"],
            },
        }

    def find_cards(self, project_id: str, payload: dict) -> dict:
        request = FindCardsPayload.model_validate(payload)
        snapshot = self.project_service.get_project_snapshot(project_id)
        timeline = self.asset_timeline_service.build(project_id, snapshot)
        timeline_cards = {item["card_id"]: item for item in timeline["cards"]}
        runs_by_card = self._runs_by_card(snapshot["graph"].runs)
        query = self._normalize_query(request.query)
        asset_id = str(request.asset_id or "").strip()
        matches = []
        for card in snapshot["cards"]:
            if request.status and card.status != request.status:
                continue
            if request.step is not None and card.step != request.step:
                continue
            if asset_id and asset_id not in self._card_asset_ids(card):
                continue
            if query and query not in self._card_search_text(card):
                continue
            matches.append(self._compact_card(card, timeline_cards.get(card.card_id), runs_by_card.get(card.card_id, [])))
        limit = max(1, min(int(request.limit or 12), 50))
        return {
            "project_id": project_id,
            "items": matches[:limit],
            "total": len(matches),
            "query": request.query,
        }

    def find_assets(self, project_id: str, payload: dict) -> dict:
        request = FindAssetsPayload.model_validate(payload)
        snapshot = self.project_service.get_project_snapshot(project_id)
        timeline = self.asset_timeline_service.build(project_id, snapshot)
        materialized = {asset.asset_id: asset for asset in snapshot["graph"].assets}
        query = self._normalize_query(request.query)
        output_contracts = self._output_contracts_by_asset(snapshot["cards"])
        matches: list[dict[str, Any]] = []
        for record in timeline["assets"]:
            asset_id = str(record.get("asset_id") or "")
            contract = output_contracts.get(asset_id)
            compact = self._compact_asset_record(record, materialized.get(asset_id), contract)
            if not self._asset_matches(compact, query=query, request=request):
                continue
            matches.append(compact)
        limit = max(1, min(int(request.limit or 12), 50))
        return {
            "project_id": project_id,
            "items": matches[:limit],
            "total": len(matches),
            "query": request.query,
        }

    def get_card_detail(self, project_id: str, card_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        card = next((item for item in snapshot["cards"] if item.card_id == card_id), None)
        if not card:
            raise ManagerPlanningError(f"Card not found: {card_id}")
        timeline = self.asset_timeline_service.build(project_id, snapshot)
        timeline_card = next((item for item in timeline["cards"] if item["card_id"] == card_id), None)
        runs = [run for run in snapshot["graph"].runs if run.card_id == card_id]
        return {
            "project_id": project_id,
            "card": card.model_dump(),
            "timeline": timeline_card,
            "runs": [self._compact_run(run) for run in runs[-8:]],
            "dependency_attention": self.dependency_attention_service.issues_for_card(snapshot, card_id),
        }

    def inspect_dependency_attention(self, project_id: str, payload: dict | None = None) -> dict:
        request = InspectDependencyAttentionPayload.model_validate(payload or {})
        snapshot = self.project_service.get_project_snapshot(project_id)
        result = self.dependency_attention_service.inspect(
            snapshot,
            card_ids=request.card_ids,
            source_card_id=request.source_card_id,
            include_recursive_downstream=request.include_recursive_downstream,
            max_issues=request.max_issues,
        )
        if any(issue.get("kind") == "input_asset_outdated" and issue.get("current_asset_id") for issue in result.get("dependency_attention", [])):
            result["repair_guidance"] = (
                "input_asset_outdated means the downstream card still saves an old inputs[].asset_id. "
                "Before rerun_card, use update_card to replace that input asset_id with current_asset_id; "
                "otherwise the new run will keep using the old asset."
            )
        return {"project_id": project_id, **result}

    def get_asset_detail(self, project_id: str, asset_id: str) -> dict:
        if not asset_id:
            raise ManagerPlanningError("get_asset_detail requires asset_id.")
        return self.result_asset_service.get_asset_detail(project_id, asset_id)

    def plan_card_write(self, project_id: str, payload: dict) -> dict:
        request = PlanCardWritePayload.model_validate(payload)
        action = str(request.action or "create").strip().lower()
        card_payload = dict(request.card or {})
        if request.card_id and "card_id" not in card_payload:
            card_payload["card_id"] = request.card_id
        snapshot = self.project_service.get_project_snapshot(project_id)
        replacing_card_id = card_payload.get("card_id") if action == "update" else None
        try:
            if action == "update":
                existing = next((item for item in snapshot["cards"] if item.card_id == str(card_payload.get("card_id") or "")), None)
                if not existing:
                    raise ManagerPlanningError(f"Card not found: {card_payload.get('card_id') or request.card_id or ''}")
                candidate = self._normalize_card_payload({**existing.model_dump(), **card_payload}, allow_missing_card_id=True)
            else:
                candidate = self._normalize_card_payload(card_payload, allow_missing_card_id=False)
                if any(item.card_id == candidate.card_id for item in snapshot["cards"]):
                    raise ManagerPlanningError(f"Duplicate card_id: {candidate.card_id}")
            normalized, errors = self.asset_timeline_service.validate_card(snapshot, candidate, replacing_card_id=replacing_card_id)
        except Exception as exc:
            message = str(exc)
            return {
                "ok": False,
                "project_id": project_id,
                "action": action,
                "errors": [message],
                "retry_hints": [self._retry_hint_for_validation_error(message)],
            }
        return {
            "ok": not errors,
            "project_id": project_id,
            "action": action,
            "card": self._compact_card(normalized, None, []),
            "normalized_outputs": [output.model_dump() for output in normalized.outputs],
            "recommended_step": self._recommended_step(snapshot, normalized),
            "errors": errors,
            "retry_hints": [self._retry_hint_for_validation_error(error) for error in errors],
        }

    def list_data_assets(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        timeline = self.asset_timeline_service.build(project_id, snapshot)
        assets_by_id = {asset.asset_id: asset for asset in snapshot["graph"].assets}
        return {
            "project_id": project_id,
            "assets": timeline["assets"],
            "cards": timeline["cards"],
            "materialized_assets": [asset.model_dump() for asset in snapshot["graph"].assets],
            "session_uploads": [asset.model_dump() for asset in snapshot["graph"].assets if self._is_session_upload(asset)],
            "workspace_files": [
                {
                    "asset_id": asset.asset_id,
                    "path": asset.path,
                    "title": asset.title,
                    "status": asset.status,
                    "asset_type": asset.asset_type,
                    "summary": asset.summary,
                }
                for asset in snapshot["graph"].assets
            ],
            "planned_assets": [
                asset
                for asset in timeline["assets"]
                if asset.get("planned") and asset.get("asset_id") not in assets_by_id
            ],
            "timeline": {
                "parallel_batches": timeline["parallel_batches"],
                "cycle_card_ids": timeline["cycle_card_ids"],
                "duplicate_output_assets": timeline["duplicate_output_assets"],
            },
            "tool_policy": self._tool_policy(snapshot),
        }

    def create_card(self, project_id: str, payload: dict) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        card = self._normalize_card_payload(payload, allow_missing_card_id=False)
        card, errors = self.asset_timeline_service.validate_card(snapshot, card)
        if errors:
            raise ManagerPlanningError("; ".join(errors))
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            if any(item.card_id == card.card_id for item in cards):
                raise ManagerPlanningError(f"Duplicate card_id: {card.card_id}")
            cards.append(card)
            graph = store.load_graph()
            self._sync_module_links(graph, card, previous_card=None)
            ModuleGroupStateService.sync_linked_module_status_from_card(card, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)
            self._audit_card_tool(project_id, "create_card", card.card_id, payload)
        return {"ok": True, "card": card.model_dump(), "card_id": card.card_id}

    def update_card(self, project_id: str, payload: dict) -> dict:
        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            raise ManagerPlanningError("update_card requires card_id.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        existing = next((item for item in snapshot["cards"] if item.card_id == card_id), None)
        if not existing:
            raise ManagerPlanningError(f"Card not found: {card_id}")
        updated = self._normalize_card_payload({**existing.model_dump(), **payload}, allow_missing_card_id=True)
        updated, errors = self.asset_timeline_service.validate_card(snapshot, updated, replacing_card_id=card_id)
        if errors:
            raise ManagerPlanningError("; ".join(errors))
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            index = next((idx for idx, item in enumerate(cards) if item.card_id == card_id), None)
            if index is None:
                raise ManagerPlanningError(f"Card not found: {card_id}")
            previous = cards[index]
            cards[index] = updated
            graph = store.load_graph()
            self._sync_module_links(graph, updated, previous_card=previous)
            # Guard: accepted cards must always have consistent output bindings.
            if updated.status == "accepted":
                try:
                    WorkerService._assert_acceptance_graph_consistent(updated, graph, previous.linked_runs[-1] if previous.linked_runs else "")
                except AssertionError as exc:
                    action = "直接设置 accepted" if previous.status != "accepted" else "保存会破坏 accepted 状态"
                    raise ManagerPlanningError(
                        f"accepted card 图一致性检查失败：{exc}。"
                        f"请先运行/审核/重新绑定输出，而不是{action}。"
                    ) from exc
            ModuleGroupStateService.sync_linked_module_status_from_card(updated, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)
            self._audit_card_tool(project_id, "update_card", card_id, payload)
        hint = self.dependency_attention_service.mutation_hint(self.project_service.get_project_snapshot(project_id), updated.card_id)
        return {"ok": True, "card": updated.model_dump(), "card_id": updated.card_id, **hint}

    def configure_card_execution(self, project_id: str, payload: dict) -> dict:
        try:
            request = ConfigureCardExecutionPayload.model_validate(payload)
        except ValidationError as exc:
            raise ManagerPlanningError(f"Invalid configure_card_execution payload: {exc}") from exc
        card_ids = [str(item).strip() for item in request.card_ids if str(item).strip()]
        if not card_ids:
            card_id = str(request.card_id or "").strip()
            if card_id:
                card_ids = [card_id]
        if not card_ids:
            raise ManagerPlanningError("configure_card_execution requires card_id or card_ids.")
        tool_policy = request.tool_policy.model_dump(exclude_none=True) if request.tool_policy else {}
        runtime_bindings = request.runtime_bindings.model_dump(exclude_none=True) if request.runtime_bindings else {}
        instruction_blocks = request.instruction_blocks
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            updated_cards: list[Card] = []
            missing = [card_id for card_id in card_ids if not any(card.card_id == card_id for card in cards)]
            if missing:
                raise ManagerPlanningError(f"Card not found: {', '.join(missing)}")
            for card in cards:
                if card.card_id not in card_ids:
                    continue
                context = card.executor_context.model_copy(deep=True) if card.executor_context else ExecutorContext()
                if request.skills is not None:
                    context.skills = [str(item).strip() for item in request.skills if str(item).strip()]
                if request.mcp_servers is not None:
                    context.mcp_servers = [str(item).strip() for item in request.mcp_servers if str(item).strip()]
                if "network" in tool_policy:
                    context.tool_policy.network = str(tool_policy["network"])
                if "python" in tool_policy:
                    context.tool_policy.python = bool(tool_policy["python"])
                if "rscript" in tool_policy:
                    context.tool_policy.rscript = bool(tool_policy["rscript"])
                if "shell" in tool_policy:
                    context.tool_policy.shell = bool(tool_policy["shell"])
                if "git_write" in tool_policy:
                    context.tool_policy.git_write = bool(tool_policy["git_write"])
                if "conda_env" in runtime_bindings:
                    context.runtime_bindings.conda_env = runtime_bindings.get("conda_env")
                if "r_env" in runtime_bindings:
                    context.runtime_bindings.r_env = runtime_bindings.get("r_env")
                if "working_dir" in runtime_bindings and runtime_bindings.get("working_dir"):
                    context.runtime_bindings.working_dir = str(runtime_bindings["working_dir"])
                env = runtime_bindings.get("env")
                if isinstance(env, dict):
                    context.runtime_bindings.env.update({str(key): str(value) for key, value in env.items() if value is not None})
                if instruction_blocks is not None:
                    existing_blocks = list(context.instruction_blocks)
                    for block in instruction_blocks:
                        block_text = str(block).strip()
                        if block_text and block_text not in existing_blocks:
                            existing_blocks.append(block_text)
                    context.instruction_blocks = existing_blocks
                card.executor_context = context
                card.progress_note = str(request.progress_note or card.progress_note or "").strip() or card.progress_note
                updated_cards.append(card)
                self._audit_card_tool(project_id, "configure_card_execution", card.card_id, payload)
            store.save_cards(cards)
        return {"cards": [card.model_dump() for card in updated_cards], "updated_card_ids": [card.card_id for card in updated_cards]}

    def delete_card(self, project_id: str, payload: dict) -> dict:
        card_id = str(payload.get("card_id") or payload.get("module_id") or "").strip()
        if not card_id:
            raise ManagerPlanningError("delete_card requires card_id.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        existing = next((item for item in snapshot["cards"] if item.card_id == card_id), None)
        if not existing:
            raise ManagerPlanningError(f"Card not found: {card_id}")
        updated = existing.model_copy(
            update={
                "status": "cancelled",
                "manager_review": str(payload.get("reason") or payload.get("message") or existing.manager_review or "").strip(),
                "next_actions": ["恢复卡片", "查看影响"],
            }
        )
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            cards = store.load_cards()
            index = next((idx for idx, item in enumerate(cards) if item.card_id == card_id), None)
            if index is None:
                raise ManagerPlanningError(f"Card not found: {card_id}")
            cards[index] = updated
            graph = store.load_graph()
            self._sync_module_links(graph, updated, previous_card=existing)
            ModuleGroupStateService.sync_linked_module_status_from_card(updated, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)
            self._audit_card_tool(project_id, "delete_card", card_id, payload)
        after_snapshot = self.project_service.get_project_snapshot(project_id)
        hint = self.dependency_attention_service.mutation_hint(after_snapshot, updated.card_id)
        return {
            "card": updated.model_dump(),
            "timeline": self.asset_timeline_service.build(project_id, after_snapshot),
            **hint,
        }

    def get_tool_policy(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        return self._tool_policy(snapshot)

    def set_tool_policy(self, project_id: str, payload: dict) -> dict:
        audit_card_tools = bool(payload.get("audit_card_tools", False))
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            graph = store.load_graph()
            graph.metadata["tool_policy"] = {"audit_card_tools": audit_card_tools}
            store.save_graph(graph)
        return {"tool_policy": {"audit_card_tools": audit_card_tools}}

    def read_result_asset(self, project_id: str, asset_id: str) -> dict:
        if not asset_id:
            raise ManagerPlanningError("read_result_asset requires asset_id.")
        return self.result_asset_service.get_asset_detail(project_id, asset_id)

    def list_skill_library(self, project_id: str) -> dict:
        payload = self.library_registry_service.list_entries("skill", minimal=True)
        payload["project_id"] = project_id
        return payload

    def list_mcp_library(self, project_id: str) -> dict:
        payload = self.library_registry_service.list_entries("mcp", minimal=True)
        payload["project_id"] = project_id
        return payload

    def search_skill_library(self, project_id: str, payload: dict) -> dict:
        query = str(payload.get("query") or payload.get("q") or "").strip()
        runtime = str(payload.get("runtime") or "").strip() or None
        tags = [str(item).strip() for item in payload.get("tags") or [] if str(item).strip()]
        top_k = int(payload.get("top_k") or payload.get("limit") or 8)
        result = self.library_registry_service.search_entries(
            "skill",
            query=query,
            runtime=runtime,
            tags=tags,
            top_k=top_k,
            minimal=True,
        )
        result["project_id"] = project_id
        return result

    def search_mcp_library(self, project_id: str, payload: dict) -> dict:
        query = str(payload.get("query") or payload.get("q") or "").strip()
        runtime = str(payload.get("runtime") or "").strip() or None
        tags = [str(item).strip() for item in payload.get("tags") or [] if str(item).strip()]
        top_k = int(payload.get("top_k") or payload.get("limit") or 8)
        result = self.library_registry_service.search_entries(
            "mcp",
            query=query,
            runtime=runtime,
            tags=tags,
            top_k=top_k,
            minimal=True,
        )
        result["project_id"] = project_id
        return result

    def get_skill_library_item(self, project_id: str, skill_id: str) -> dict:
        try:
            payload = self.library_registry_service.get_entry("skill", skill_id)
        except ValueError as exc:
            raise ManagerPlanningError(str(exc)) from exc
        payload["project_id"] = project_id
        return payload

    def get_mcp_library_item(self, project_id: str, entry_id: str) -> dict:
        try:
            payload = self.library_registry_service.get_entry("mcp", entry_id)
        except ValueError as exc:
            raise ManagerPlanningError(str(exc)) from exc
        payload["project_id"] = project_id
        return payload

    def install_runtime_dependencies(self, project_id: str, payload: dict) -> dict:
        if self.runtime_dependency_job_service is None:
            raise ManagerPlanningError("runtime dependency job service is unavailable.")
        request_payload = self._validated_runtime_dependency_payload(payload)
        job = self.runtime_dependency_job_service.submit(
            project_id,
            request_payload,
            self._install_runtime_dependencies_sync,
        )
        return {
            "ok": True,
            "background": True,
            "job_id": job.job_id,
            "status": job.status,
            "ecosystem": request_payload["ecosystem"],
            "runtime": request_payload["runtime"],
            "packages": request_payload["packages"],
            "manager": request_payload["manager"],
            "message": (
                f"Started background dependency installation for {request_payload['runtime']} "
                f"({len(request_payload['packages'])} package{'s' if len(request_payload['packages']) != 1 else ''})."
            ),
            "created_at": job.created_at,
        }

    def get_runtime_dependency_install_status(self, project_id: str, job_id: str) -> dict:
        if self.runtime_dependency_job_service is None:
            raise ManagerPlanningError("runtime dependency job service is unavailable.")
        job = self.runtime_dependency_job_service.get(job_id)
        if job is None or job.project_id != project_id:
            raise ManagerPlanningError("Runtime dependency job not found.")
        result = job.result or {}
        payload = {
            "job_id": job.job_id,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "payload": job.payload,
            "result": result or None,
            "error": job.error,
        }
        if result:
            payload.update(
                {
                    "ok": bool(result.get("ok")),
                    "message": result.get("message"),
                    "runtime": result.get("runtime"),
                    "resolved_runtime": result.get("resolved_runtime"),
                    "packages": result.get("packages"),
                    "manager": result.get("manager"),
                    "stdout_tail": result.get("stdout_tail"),
                    "stderr_tail": result.get("stderr_tail"),
                }
            )
        return payload

    def _install_runtime_dependencies_sync(self, project_id: str, payload: dict) -> dict:
        request = InstallRuntimeDependenciesPayload.model_validate(payload)
        ecosystem = self._normalize_dependency_ecosystem(request.ecosystem)
        packages = self._validate_dependency_packages(ecosystem, request.packages)
        runtime = str(request.runtime or "").strip()
        if not runtime or runtime == "__system__":
            raise ManagerPlanningError("install_runtime_dependencies requires a selected non-system runtime.")
        timeout = max(30, min(int(request.timeout_seconds or 600), 1800))
        if ecosystem == "python":
            command, resolved_runtime = self._python_dependency_command(runtime, packages, request.manager)
        else:
            command, resolved_runtime = self._r_dependency_command(runtime, packages, request.manager)
        started_at = utc_now()
        try:
            result = subprocess.run(
                command,
                cwd=self.project_service.project_path(project_id),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "ecosystem": ecosystem,
                "runtime": runtime,
                "resolved_runtime": resolved_runtime,
                "packages": packages,
                "manager": self._dependency_manager_label(ecosystem, request.manager),
                "message": f"Dependency installation timed out after {timeout} seconds.",
                "stdout_tail": self._tail_text(exc.stdout),
                "stderr_tail": self._tail_text(exc.stderr),
                "started_at": started_at,
                "finished_at": utc_now(),
            }
        except OSError as exc:
            return {
                "ok": False,
                "ecosystem": ecosystem,
                "runtime": runtime,
                "resolved_runtime": resolved_runtime,
                "packages": packages,
                "manager": self._dependency_manager_label(ecosystem, request.manager),
                "message": f"Dependency installation could not start: {exc}",
                "stdout_tail": "",
                "stderr_tail": str(exc),
                "started_at": started_at,
                "finished_at": utc_now(),
            }
        ok = result.returncode == 0
        return {
            "ok": ok,
            "ecosystem": ecosystem,
            "runtime": runtime,
            "resolved_runtime": resolved_runtime,
            "packages": packages,
            "manager": self._dependency_manager_label(ecosystem, request.manager),
            "returncode": result.returncode,
            "message": "Dependencies installed." if ok else "Dependency installation failed; ask the user to prepare the runtime manually if the error is not immediately fixable.",
            "stdout_tail": self._tail_text(result.stdout),
            "stderr_tail": self._tail_text(result.stderr),
            "started_at": started_at,
            "finished_at": utc_now(),
        }

    def _validated_runtime_dependency_payload(self, payload: dict) -> dict[str, Any]:
        request = InstallRuntimeDependenciesPayload.model_validate(payload)
        ecosystem = self._normalize_dependency_ecosystem(request.ecosystem)
        packages = self._validate_dependency_packages(ecosystem, request.packages)
        runtime = str(request.runtime or "").strip()
        if not runtime or runtime == "__system__":
            raise ManagerPlanningError("install_runtime_dependencies requires a selected non-system runtime.")
        timeout = max(30, min(int(request.timeout_seconds or 600), 1800))
        return {
            "ecosystem": ecosystem,
            "runtime": runtime,
            "packages": packages,
            "manager": self._dependency_manager_label(ecosystem, request.manager),
            "timeout_seconds": timeout,
            "source": dict(request.source or {}),
        }

    def start_card_run(self, project_id: str, payload: dict) -> dict:
        if self.worker_service is None:
            raise ManagerPlanningError("worker service is unavailable for start_card_run.")
        request = StartCardRunPayload.model_validate(payload)
        try:
            response = self.worker_service.start_run(
                project_id,
                request.card_id,
                worker_type=request.worker_type,
                profile_id=request.profile_id,
                python_runtime=request.python_runtime,
                r_runtime=request.r_runtime,
            )
            return {
                "ok": True,
                "can_start": True,
                "background": True,
                "async_boundary": True,
                "do_not_poll": True,
                "wait_for_wake": True,
                "message": "Run started in the background. Do not poll card status in this turn; wait for run events or a wake event.",
                **response,
            }
        except HTTPException as exc:
            if exc.status_code == 409:
                detail = exc.detail
                block_details = detail.get("block_details") if isinstance(detail, dict) else {}
                return {
                    "ok": False,
                    "can_start": False,
                    "message": detail.get("message") if isinstance(detail, dict) else str(exc.detail),
                    "pending_approvals": [],
                    "rejected_approvals": [],
                    "block_reasons": block_details.get("block_reasons") if isinstance(block_details, dict) else [],
                    "block_details": block_details,
                }
            raise ManagerPlanningError(str(exc.detail)) from exc

    def stop_card_run(self, project_id: str, payload: dict) -> dict:
        if self.worker_service is None:
            raise ManagerPlanningError("worker service is unavailable for stop_card_run.")
        request = StopCardRunPayload.model_validate(payload)
        run_id = request.run_id or self._active_run_id_for_card(project_id, request.card_id)
        if not run_id:
            return {
                "ok": False,
                "stopped": False,
                "message": "No active run found for the requested card.",
            }
        try:
            response = self.worker_service.cancel_run(project_id, run_id, reason=request.reason)
        except HTTPException as exc:
            raise ManagerPlanningError(str(exc.detail)) from exc
        return {"ok": True, "stopped": True, **response}

    def rerun_card(self, project_id: str, payload: dict) -> dict:
        if self.worker_service is None:
            raise ManagerPlanningError("worker service is unavailable for rerun_card.")
        request = StartCardRunPayload.model_validate(payload)
        try:
            response = self.worker_service.rerun_card(
                project_id,
                request.card_id,
                worker_type=request.worker_type,
                profile_id=request.profile_id,
                python_runtime=request.python_runtime,
                r_runtime=request.r_runtime,
            )
            return {
                "ok": True,
                "can_start": True,
                "background": True,
                "async_boundary": True,
                "do_not_poll": True,
                "wait_for_wake": True,
                "message": "Rerun started in the background. Do not poll card status in this turn; wait for run events or a wake event.",
                **response,
            }
        except HTTPException as exc:
            raise ManagerPlanningError(str(exc.detail)) from exc

    def review_card_run(self, project_id: str, payload: dict) -> dict:
        if self.worker_service is None:
            raise ManagerPlanningError("worker service is unavailable for review_card_run.")
        request = ReviewCardRunPayload.model_validate(payload)
        run_id = request.run_id or self._latest_run_id_for_card(project_id, request.card_id)
        if not run_id:
            raise ManagerPlanningError("review_card_run requires run_id or a card with linked runs.")
        try:
            response = self.worker_service.review_run(project_id, run_id, accept=request.accept)
        except Exception as exc:
            raise ManagerPlanningError(str(exc)) from exc
        return {"ok": True, **response}

    def cleanup_run_history(self, project_id: str, payload: dict) -> dict:
        if self.worker_service is None:
            raise ManagerPlanningError("worker service is unavailable for cleanup_run_history.")
        request = CleanupRunHistoryPayload.model_validate(payload)
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph = snapshot["graph"]
        requested_statuses = {str(status).strip() for status in request.statuses if str(status).strip()}
        default_statuses = {"failed", "cancelled", "reviewed"}
        statuses = requested_statuses or default_statuses
        valid_asset_run_ids = {
            asset.created_by_run
            for asset in graph.assets
            if asset.created_by_run and asset.status == "valid"
        }
        latest_by_card: dict[str, str] = {}
        for run in sorted(graph.runs, key=lambda item: item.started_at or item.finished_at or ""):
            latest_by_card[run.card_id] = run.run_id

        candidates = []
        for run in graph.runs:
            if request.run_id and run.run_id != request.run_id:
                continue
            if request.card_id and run.card_id != request.card_id:
                continue
            if run.status not in statuses:
                continue
            if run.cleanup_status == "completed":
                continue
            if request.keep_latest_per_card and not request.run_id and latest_by_card.get(run.card_id) == run.run_id:
                continue
            candidates.append(run)

        cleaned: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for run in candidates:
            if run.run_id in valid_asset_run_ids and not request.include_valid_assets:
                skipped.append(
                    {
                        "run_id": run.run_id,
                        "card_id": run.card_id,
                        "status": run.status,
                        "reason": "valid_assets_protected",
                    }
                )
                continue
            if request.dry_run:
                cleaned.append(
                    {
                        "run_id": run.run_id,
                        "card_id": run.card_id,
                        "status": run.status,
                        "dry_run": True,
                    }
                )
                continue
            try:
                response = self.worker_service.cleanup_run(
                    project_id,
                    run.run_id,
                    reason=request.reason or "Cleaned by Manager cleanup_run_history.",
                )
            except HTTPException as exc:
                skipped.append(
                    {
                        "run_id": run.run_id,
                        "card_id": run.card_id,
                        "status": run.status,
                        "reason": str(exc.detail),
                    }
                )
                continue
            cleaned.append({"card_id": run.card_id, **response})

        return {
            "ok": True,
            "dry_run": request.dry_run,
            "requested": {
                "run_id": request.run_id,
                "card_id": request.card_id,
                "statuses": sorted(statuses),
                "keep_latest_per_card": request.keep_latest_per_card,
                "include_valid_assets": request.include_valid_assets,
            },
            "cleaned": cleaned,
            "skipped": skipped,
            "cleaned_count": len(cleaned),
            "skipped_count": len(skipped),
            "message": f"Cleaned {len(cleaned)} run history item(s); skipped {len(skipped)}.",
        }

    def search_card_templates(self, project_id: str, payload: dict) -> dict:
        request = SearchCardTemplatesPayload.model_validate(payload)
        templates = self._load_card_templates()
        matches = self._match_templates(templates, request.query, request.tags, request.card_type)
        limit = max(1, min(int(request.limit or 5), 20))
        return {
            "project_id": project_id,
            "items": [item for item in matches[:limit]],
            "total": len(matches),
        }

    def save_card_template(self, project_id: str, payload: dict) -> dict:
        request = SaveCardTemplatePayload.model_validate(payload)
        snapshot = self.project_service.get_project_snapshot(project_id)
        card = next((item for item in snapshot["cards"] if item.card_id == request.card_id), None)
        if card is None:
            raise ManagerPlanningError(f"Card not found: {request.card_id}")
        run_id = self._latest_run_id_for_card(project_id, card.card_id)
        manifest = self._load_manifest_if_exists(project_id, run_id) if run_id else None
        if card.status != "accepted" and not self._run_reviewer_passed(project_id, run_id):
            raise ManagerPlanningError("Card template can only be saved from an accepted card or a run with reviewer pass.")

        template = self._build_card_template(project_id, card, manifest, request)
        templates = self._load_card_templates()
        templates = [item for item in templates if item.template_id != template.template_id]
        templates.append(template)
        self._save_card_templates(templates)
        self._save_template_bundle(template)
        return {"ok": True, "template": template.model_dump()}

    def instantiate_card_template(self, project_id: str, payload: dict) -> dict:
        request = InstantiateCardTemplatePayload.model_validate(payload)
        template = self._load_card_template(request.template_id)
        snapshot = self.project_service.get_project_snapshot(project_id)
        card_payload = self._instantiate_card_payload(project_id, snapshot, template, request)
        result = self.create_card(project_id, card_payload)
        templates = self._load_card_templates()
        for index, item in enumerate(templates):
            if item.template_id != template.template_id:
                continue
            templates[index] = item.model_copy(
                update={
                    "reuse_count": item.reuse_count + 1,
                    "updated_at": utc_now(),
                }
            )
            break
        self._save_card_templates(templates)
        return {"template_id": template.template_id, **result}

    def list_project_memory(self, project_id: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        kind = str(payload.get("kind") or "").strip()
        if kind and kind not in {"user_preference", "correction_memory"}:
            raise ManagerPlanningError("memory kind must be user_preference or correction_memory.")
        query = str(payload.get("query") or "").strip().lower()
        limit = self._memory_limit(payload.get("limit"))
        items = self.project_service.graph_store(project_id).load_project_memory()
        if kind:
            items = [item for item in items if item.kind == kind]
        if query:
            items = [item for item in items if query in item.summary.lower()]
        items = sorted(items, key=lambda item: item.updated_at, reverse=True)[:limit]
        summary_lines = [f"- {item.kind}: {item.summary}" for item in items]
        return {
            "project_id": project_id,
            "items": [item.model_dump() for item in items],
            "summary": "\n".join(summary_lines),
            "memory_policy": {
                "fact_source": "Use blueprint/cards/assets/runs for project execution facts.",
                "scope": "Project memory stores only explicit user preferences and corrections.",
            },
        }

    def write_project_memory(self, project_id: str, payload: dict) -> dict:
        kind = str(payload.get("kind") or "").strip()
        if kind not in {"user_preference", "correction_memory"}:
            raise ManagerPlanningError("write_project_memory requires kind user_preference or correction_memory.")
        summary = re.sub(r"\s+", " ", str(payload.get("summary") or "")).strip()
        if not summary:
            raise ManagerPlanningError("write_project_memory requires summary.")
        if len(summary) > 500:
            summary = summary[:497].rstrip() + "..."
        source = str(payload.get("source") or "manager_chat").strip() or "manager_chat"
        confidence = self._memory_confidence(payload.get("confidence"))
        memory_id = str(payload.get("memory_id") or "").strip() or self._memory_id(kind, summary)
        now = utc_now()
        with self.project_service.lock_for(project_id):
            store = self.project_service.graph_store(project_id)
            items = store.load_project_memory()
            existing_index = next((idx for idx, item in enumerate(items) if item.memory_id == memory_id), None)
            if existing_index is None:
                item = ProjectMemoryItem(
                    memory_id=memory_id,
                    kind=kind,
                    summary=summary,
                    source=source,
                    confidence=confidence,
                    created_at=now,
                    updated_at=now,
                )
                items.append(item)
            else:
                previous = items[existing_index]
                item = previous.model_copy(
                    update={
                        "kind": kind,
                        "summary": summary,
                        "source": source,
                        "confidence": confidence,
                        "updated_at": now,
                    }
                )
                items[existing_index] = item
            store.save_project_memory(items)
        return {"memory": item.model_dump(), "items_count": len(items)}

    @staticmethod
    def _normalize_card_payload(payload: dict, allow_missing_card_id: bool) -> Card:
        card_id = str(payload.get("card_id") or "").strip()
        if not card_id and not allow_missing_card_id:
            raise ManagerPlanningError("card_id is required.")
        inputs = [item if isinstance(item, CardAssetRef) else CardAssetRef.model_validate(item) for item in payload.get("inputs") or []]
        outputs = [item if isinstance(item, CardOutputSpec) else CardOutputSpec.model_validate(item) for item in payload.get("outputs") or []]
        card_payload = {
            "card_id": card_id,
            "card_type": payload.get("card_type") or "module",
            "title": str(payload.get("title") or "").strip(),
            "status": payload.get("status") or "planned",
            "step": payload.get("step"),
            "summary": str(payload.get("summary") or "").strip(),
            "why": str(payload.get("why") or "").strip(),
            "inputs": [item.model_dump() for item in inputs],
            "outputs": [item.model_dump() for item in outputs],
            "key_findings": list(payload.get("key_findings") or []),
            "manager_review": str(payload.get("manager_review") or "").strip(),
            "next_actions": list(payload.get("next_actions") or []),
            "linked_modules": list(payload.get("linked_modules") or []),
            "linked_runs": list(payload.get("linked_runs") or []),
            "linked_assets": list(payload.get("linked_assets") or []),
            "progress_note": payload.get("progress_note"),
            "executor_context": payload.get("executor_context"),
        }
        if not card_payload["title"]:
            raise ManagerPlanningError("card title is required.")
        if not card_payload["summary"]:
            raise ManagerPlanningError("card summary is required.")
        return Card.model_validate(card_payload)

    @staticmethod
    def _tool_policy(snapshot: dict) -> dict:
        metadata = snapshot["graph"].metadata if snapshot.get("graph") else {}
        policy = metadata.get("tool_policy") if isinstance(metadata, dict) else {}
        if not isinstance(policy, dict):
            policy = {}
        return {"audit_card_tools": bool(policy.get("audit_card_tools", False))}

    def _audit_card_tool(self, project_id: str, action: str, card_id: str, payload: dict) -> None:
        if not self._tool_policy(self.project_service.get_project_snapshot(project_id))["audit_card_tools"]:
            return
        store = self.project_service.graph_store(project_id)
        graph = store.load_graph()
        audit_log = list(graph.metadata.get("card_tool_audit") or [])
        audit_log.append(
            {
                "action": action,
                "card_id": card_id,
                "payload": payload,
                "created_at": utc_now(),
            }
        )
        graph.metadata["card_tool_audit"] = audit_log
        store.save_graph(graph)

    @staticmethod
    def _memory_id(kind: str, summary: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", summary.lower()).strip("_")[:32] or "memory"
        digest = sha256(f"{kind}:{summary}".encode("utf-8")).hexdigest()[:10]
        return f"{kind}_{slug}_{digest}"

    @staticmethod
    def _memory_limit(value: object) -> int:
        try:
            limit = int(value or 5)
        except (TypeError, ValueError):
            limit = 5
        return max(1, min(limit, 10))

    @staticmethod
    def _memory_confidence(value: object) -> float:
        try:
            confidence = float(value if value is not None else 1.0)
        except (TypeError, ValueError):
            confidence = 1.0
        return max(0.0, min(confidence, 1.0))

    @staticmethod
    def _normalize_dependency_ecosystem(ecosystem: str) -> str:
        normalized = str(ecosystem or "").strip().lower()
        if normalized == "python":
            return "python"
        if normalized == "r":
            return "R"
        raise ManagerPlanningError("install_runtime_dependencies ecosystem must be python or R.")

    @staticmethod
    def _validate_dependency_packages(ecosystem: str, packages: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in packages if str(item).strip()]
        if not cleaned:
            raise ManagerPlanningError("install_runtime_dependencies requires at least one package.")
        if len(cleaned) > 40:
            raise ManagerPlanningError("install_runtime_dependencies accepts at most 40 packages per call.")
        python_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(\[[A-Za-z0-9_,.-]+\])?([<>=!~]=?[A-Za-z0-9_.+*-]+)?$")
        r_re = re.compile(r"^[A-Za-z][A-Za-z0-9.]*$")
        invalid = [
            item
            for item in cleaned
            if not (python_re.fullmatch(item) if ecosystem == "python" else r_re.fullmatch(item))
        ]
        if invalid:
            raise ManagerPlanningError(f"Unsupported package specifier(s): {', '.join(invalid)}")
        return list(dict.fromkeys(cleaned))

    def _python_dependency_command(self, runtime: str, packages: list[str], manager: str | None) -> tuple[list[str], str]:
        manager_name = self._dependency_manager_label("python", manager)
        conda_base, env_path = CommandTemplateWorkerAdapter._resolve_conda_runtime(runtime, self.project_service.settings)
        if not env_path.exists():
            raise ManagerPlanningError(f"Python runtime not found: {runtime}")
        if manager_name == "conda":
            conda_bin = conda_base / "bin" / "conda"
            if not conda_bin.exists():
                raise ManagerPlanningError(f"conda executable not found for runtime: {runtime}")
            return [str(conda_bin), "install", "-y", "-p", str(env_path), *packages], str(env_path)
        python_bin = env_path / "bin" / "python"
        if not python_bin.exists():
            raise ManagerPlanningError(f"Python executable not found for runtime: {runtime}")
        return [str(python_bin), "-m", "pip", "install", *packages], str(env_path)

    def _r_dependency_command(self, runtime: str, packages: list[str], manager: str | None) -> tuple[list[str], str]:
        manager_name = self._dependency_manager_label("R", manager)
        rscript = CommandTemplateWorkerAdapter._resolve_rscript_runtime(runtime, self.project_service.settings)
        if rscript is None or not rscript.exists():
            raise ManagerPlanningError(f"R runtime not found: {runtime}")
        package_vector = "c(" + ", ".join(json.dumps(item) for item in packages) + ")"
        if manager_name == "cran":
            expression = (
                'options(repos=c(CRAN="https://cloud.r-project.org")); '
                f"install.packages({package_vector}, dependencies=TRUE)"
            )
        else:
            expression = (
                'options(repos=c(CRAN="https://cloud.r-project.org")); '
                'if (!requireNamespace("BiocManager", quietly=TRUE)) install.packages("BiocManager"); '
                f"BiocManager::install({package_vector}, ask=FALSE, update=FALSE)"
            )
        return [str(rscript), "--vanilla", "-e", expression], str(rscript)

    @staticmethod
    def _dependency_manager_label(ecosystem: str, manager: str | None) -> str:
        normalized = str(manager or "").strip().lower()
        if ecosystem == "python":
            return "conda" if normalized in {"conda", "mamba", "micromamba"} else "pip"
        return "cran" if normalized == "cran" else "bioconductor"

    @staticmethod
    def _tail_text(value: object, limit: int = 6000) -> str:
        if value is None:
            return ""
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
        return text[-limit:]

    def _active_run_id_for_card(self, project_id: str, card_id: str | None) -> str | None:
        if not card_id:
            return None
        graph = self.project_service.graph_store(project_id).load_graph()
        active = [
            run.run_id
            for run in graph.runs
            if run.card_id == card_id and run.status in {"queued", "needs_approval", "running", "reviewing"}
        ]
        return active[-1] if active else None

    def _latest_run_id_for_card(self, project_id: str, card_id: str | None) -> str | None:
        if not card_id:
            return None
        snapshot = self.project_service.get_project_snapshot(project_id)
        card = next((item for item in snapshot["cards"] if item.card_id == card_id), None)
        if card and card.linked_runs:
            return card.linked_runs[-1]
        run_ids = [run.run_id for run in snapshot["graph"].runs if run.card_id == card_id]
        return run_ids[-1] if run_ids else None

    def _run_reviewer_passed(self, project_id: str, run_id: str | None) -> bool:
        if not run_id:
            return False
        path = self.project_service.project_path(project_id) / "runs" / run_id / "executor_validation.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        reviewer = payload.get("reviewer") if isinstance(payload, dict) else {}
        if not isinstance(reviewer, dict):
            return False
        return str(reviewer.get("verdict") or "") == "pass"

    def _load_manifest_if_exists(self, project_id: str, run_id: str | None) -> Manifest | None:
        if not run_id:
            return None
        path = self.project_service.project_path(project_id) / "runs" / run_id / "manifest.json"
        if not path.exists():
            return None
        return Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _template_store_root(self) -> Path:
        root = self.project_service.settings.data_root / "_card_templates"
        (root / "bundles").mkdir(parents=True, exist_ok=True)
        return root

    def _template_index_path(self) -> Path:
        return self._template_store_root() / "templates.json"

    def _template_bundle_dir(self, template_id: str) -> Path:
        return self._template_store_root() / "bundles" / template_id

    def _load_card_templates(self) -> list[CardTemplate]:
        path = self._template_index_path()
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [CardTemplate.model_validate(item) for item in payload]

    def _save_card_templates(self, templates: list[CardTemplate]) -> None:
        atomic_write_json(self._template_index_path(), [item.model_dump() for item in templates])

    def _load_card_template(self, template_id: str) -> CardTemplate:
        template = next((item for item in self._load_card_templates() if item.template_id == template_id), None)
        if template is None:
            raise ManagerPlanningError(f"Card template not found: {template_id}")
        spec_path = self._template_bundle_dir(template_id) / "spec.json"
        if spec_path.exists():
            try:
                payload = json.loads(spec_path.read_text(encoding="utf-8"))
                template = CardTemplate.model_validate(payload)
            except json.JSONDecodeError as exc:
                raise ManagerPlanningError(f"Card template spec is invalid JSON: {template_id}") from exc
        return template

    def _save_template_bundle(self, template: CardTemplate) -> None:
        bundle_dir = self._template_bundle_dir(template.template_id)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(bundle_dir / "spec.json", template.model_dump())

    def _match_templates(
        self,
        templates: list[CardTemplate],
        query: str,
        tags: list[str],
        card_type: str | None,
    ) -> list[dict[str, Any]]:
        compact_query = query.strip().lower()
        tag_set = {item.strip().lower() for item in tags if item.strip()}
        matches: list[dict[str, Any]] = []
        for template in templates:
            if card_type and template.card_type != card_type:
                continue
            if tag_set and not tag_set.intersection({tag.lower() for tag in template.tags}):
                continue
            haystack = " ".join([template.title, template.summary, " ".join(template.tags), template.spec.summary_template]).lower()
            score = 0.2 + template.confidence_score
            if compact_query:
                for token in compact_query.split():
                    if token in haystack:
                        score += 0.4
            if compact_query and score <= 0.2 + template.confidence_score:
                continue
            matches.append(
                {
                    "template_id": template.template_id,
                    "title": template.title,
                    "summary": template.summary,
                    "tags": template.tags,
                    "card_type": template.card_type,
                    "status": template.status,
                    "confidence_score": round(min(score, 5.0), 3),
                    "reuse_count": template.reuse_count,
                    "script_asset_requirements": [item.model_dump() for item in template.spec.bundle.script_asset_requirements],
                }
            )
        matches.sort(key=lambda item: (item["confidence_score"], item["reuse_count"]), reverse=True)
        return matches

    def _build_card_template(
        self,
        project_id: str,
        card: Card,
        manifest: Manifest | None,
        request: SaveCardTemplatePayload,
    ) -> CardTemplate:
        now = utc_now()
        template_title = (request.title or card.title).strip()
        template_summary = (request.summary or card.summary).strip()
        template_id = self._slugged_template_id(template_title)
        context = card.executor_context.model_copy(deep=True) if card.executor_context else ExecutorContext()
        context.script_asset_bindings = []
        context.template_metadata = {}
        bundle = TemplateBundle(
            script_asset_requirements=[item.model_copy(deep=True) for item in context.script_asset_requirements],
            script_asset_bindings=[],
        )
        template = CardTemplate(
            template_id=template_id,
            title=template_title,
            summary=template_summary,
            tags=[item for item in request.tags if item.strip()],
            card_type=card.card_type,
            source_card_type=card.card_type,
            created_at=now,
            updated_at=now,
            last_verified_at=now if manifest is not None else None,
            confidence_score=0.8 if card.status == "accepted" else 0.7,
            status="active",
            source_card_id=card.card_id,
            source_project_id=project_id,
            spec=TemplateSpec(
                card_title_pattern=card.title,
                summary_template=card.summary,
                why_template=card.why,
                inputs_schema=[
                    TemplateIoBinding(label=item.label, status=item.status, required=True)
                    for item in card.inputs
                ],
                outputs_schema=[
                    TemplateIoBinding(
                        label=item.label,
                        role=item.role,
                        artifact_class=item.artifact_class,
                        accepted_formats=list(item.accepted_formats),
                        preferred_format=item.preferred_format,
                        asset_id=item.asset_id,
                        status=item.status,
                        required=item.required,
                        description=item.description,
                    )
                    for item in card.outputs
                ],
                executor_context=context,
                tool_policy=context.tool_policy.model_dump(),
                runtime_bindings=context.runtime_bindings.model_dump(),
                instruction_blocks=list(context.instruction_blocks),
                prompt_blocks=list(context.instruction_blocks),
                expected_artifacts=[item.role for item in manifest.created_assets] if manifest else [item.role for item in card.outputs],
                success_signals=list(card.key_findings or (manifest.key_findings if manifest else [])),
                failure_signals=list(manifest.warnings if manifest else []),
                bundle=bundle,
            ),
        )
        if manifest is not None:
            self._copy_template_code_artifacts(project_id, template, manifest)
        return template

    def _copy_template_code_artifacts(self, project_id: str, template: CardTemplate, manifest: Manifest) -> None:
        project_root = self.project_service.project_path(project_id)
        bundle_dir = self._template_bundle_dir(template.template_id)
        files_dir = bundle_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        for artifact in manifest.code_artifacts:
            try:
                source = resolve_within(project_root, artifact.path)
            except ValueError:
                continue
            if not source.is_file():
                continue
            stored_path = Path("files") / artifact.path
            target = bundle_dir / stored_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            template.spec.bundle.files.append(
                TemplateBundleFile(
                    original_path=artifact.path,
                    stored_path=stored_path.as_posix(),
                    description=getattr(artifact, "description", None),
                )
            )
            template.spec.bundle.path_rewrites[artifact.path] = stored_path.as_posix()

    def _instantiate_card_payload(
        self,
        project_id: str,
        snapshot: dict[str, Any],
        template: CardTemplate,
        request: InstantiateCardTemplatePayload,
    ) -> dict[str, Any]:
        context = template.spec.executor_context.model_copy(deep=True)
        context.template_metadata = {
            "template_id": template.template_id,
            "instantiated_at": utc_now(),
        }
        copied_references = self._copy_template_bundle_into_project(project_id, request.card_id, template)
        context.references.extend(copied_references)
        asset_map = {asset.asset_id: asset for asset in snapshot["graph"].assets}
        bindings: list[ExecutorScriptAssetBinding] = []
        for binding_request in request.script_asset_bindings:
            asset = asset_map.get(binding_request.asset_id)
            if asset is None:
                raise ManagerPlanningError(f"Script asset not found: {binding_request.asset_id}")
            bindings.append(
                ExecutorScriptAssetBinding(
                    requirement_id=binding_request.requirement_id,
                    asset_id=asset.asset_id,
                    path=asset.path,
                    title=asset.title,
                    bound_at=utc_now(),
                )
            )
            context.references.append(
                ExecutorReference(
                    type="file",
                    path=asset.path,
                    description=f"Script asset binding for {binding_request.requirement_id}: {asset.title}",
                )
            )
        context.script_asset_bindings = bindings
        if request.runtime_overrides:
            override_context = ExecutorContext.model_validate(request.runtime_overrides)
            context = WorkerService._merge_executor_context(context, override_context)
        missing_requirement_ids = {
            requirement.requirement_id
            for requirement in context.script_asset_requirements
            if requirement.requirement_id and not requirement.optional
        } - {
            binding.requirement_id
            for binding in context.script_asset_bindings
            if binding.requirement_id and (binding.asset_id or binding.path)
        }
        next_actions = ["运行前检查输入输出与运行时配置。"]
        progress_note = None
        if missing_requirement_ids:
            progress_note = f"Missing script asset bindings: {', '.join(sorted(missing_requirement_ids))}"
            next_actions.insert(0, "绑定脚本资产后再运行。")
            context.instruction_blocks.append(
                f"Do not run until these script asset bindings are resolved: {', '.join(sorted(missing_requirement_ids))}."
            )
        return {
            "card_id": request.card_id,
            "card_type": template.card_type,
            "title": (request.title or template.spec.card_title_pattern).strip(),
            "status": "planned",
            "step": request.step,
            "summary": template.spec.summary_template,
            "why": template.spec.why_template,
            "inputs": request.input_bindings or [{"label": item.label, "status": item.status} for item in template.spec.inputs_schema],
            "outputs": request.output_bindings
            or [
                {
                    "role": item.role,
                    "label": item.label,
                    "artifact_class": item.artifact_class,
                    "accepted_formats": list(item.accepted_formats),
                    "preferred_format": item.preferred_format,
                    "asset_id": None,
                    "status": "planned",
                    "required": item.required,
                    "description": item.description,
                }
                for item in template.spec.outputs_schema
            ],
            "key_findings": [],
            "manager_review": f"Instantiated from card template {template.template_id}.",
            "next_actions": next_actions,
            "progress_note": progress_note,
            "executor_context": context.model_dump(),
        }

    def _copy_template_bundle_into_project(self, project_id: str, card_id: str, template: CardTemplate) -> list[ExecutorReference]:
        bundle_dir = self._template_bundle_dir(template.template_id)
        project_root = self.project_service.project_path(project_id)
        target_root = project_root / "scripts" / "curated" / "templates" / card_id
        references: list[ExecutorReference] = []
        for file_entry in template.spec.bundle.files:
            source = bundle_dir / file_entry.stored_path
            if not source.is_file():
                continue
            target = target_root / Path(file_entry.original_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            relative_target = target.relative_to(project_root).as_posix()
            references.append(
                ExecutorReference(
                    type="file",
                    path=relative_target,
                    description=file_entry.description or f"Template file from {template.template_id}",
                )
            )
        return references

    @staticmethod
    def _runs_by_card(runs) -> dict[str, list[Any]]:
        grouped: dict[str, list[Any]] = {}
        for run in runs:
            grouped.setdefault(run.card_id, []).append(run)
        return grouped

    @staticmethod
    def _compact_run(run) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "card_id": run.card_id,
            "status": run.status,
            "title": run.title,
            "summary": run.summary,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "needs_manager_attention": run.needs_manager_attention,
        }

    @classmethod
    def _compact_card(cls, card: Card, timeline_card: dict[str, Any] | None, runs: list[Any]) -> dict[str, Any]:
        active_run = next((run for run in reversed(runs) if run.status in {"queued", "running", "reviewing", "needs_approval"}), None)
        latest_run = runs[-1] if runs else None
        required_asset_ids = timeline_card.get("required_asset_ids", []) if timeline_card else [item.asset_id for item in card.inputs if item.asset_id]
        produced_asset_ids = (
            timeline_card.get("produced_asset_ids", [])
            if timeline_card
            else [item.asset_id for item in card.outputs if item.asset_id]
        )
        return {
            "card_id": card.card_id,
            "title": card.title,
            "status": card.status,
            "card_type": card.card_type,
            "step": card.step,
            "summary": card.summary,
            "progress_note": card.progress_note,
            "required_asset_ids": required_asset_ids,
            "produced_asset_ids": produced_asset_ids,
            "depends_on_card_ids": timeline_card.get("depends_on_card_ids", []) if timeline_card else [],
            "active_run": cls._compact_run(active_run) if active_run else None,
            "latest_run": cls._compact_run(latest_run) if latest_run else None,
            "blockers": cls._card_blockers(card, required_asset_ids),
        }

    @staticmethod
    def _card_blockers(card: Card, required_asset_ids: list[str]) -> list[str]:
        blockers: list[str] = []
        if card.status in {"failed", "rejected"}:
            blockers.append(f"card_status:{card.status}")
        if card.progress_note and re.search(r"\b(missing|failed|blocked|缺少|失败|阻塞)\b", card.progress_note, re.I):
            blockers.append(card.progress_note)
        if any(item.asset_id is None for item in card.inputs):
            blockers.append("input_without_asset_id")
        if not required_asset_ids and card.inputs:
            blockers.append("input_asset_not_resolved")
        return blockers

    @classmethod
    def _project_blockers(cls, snapshot: dict[str, Any], timeline: dict[str, Any]) -> list[dict[str, Any]]:
        asset_ids = {str(asset.get("asset_id")) for asset in timeline["assets"] if asset.get("asset_id")}
        blockers: list[dict[str, Any]] = []
        for card in snapshot["cards"]:
            missing_inputs = [item.asset_id for item in card.inputs if item.asset_id and item.asset_id not in asset_ids]
            card_blockers = cls._card_blockers(card, [item.asset_id for item in card.inputs if item.asset_id])
            if missing_inputs:
                card_blockers.append(f"missing_inputs:{', '.join(missing_inputs)}")
            if card.card_id in timeline["cycle_card_ids"]:
                card_blockers.append("dependency_cycle")
            if card_blockers:
                blockers.append({"card_id": card.card_id, "title": card.title, "blockers": card_blockers})
        for asset_id in timeline["duplicate_output_assets"]:
            blockers.append({"asset_id": asset_id, "blockers": ["duplicate_output_asset_id"]})
        return blockers

    @staticmethod
    def _normalize_query(value: str | None) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    @classmethod
    def _card_search_text(cls, card: Card) -> str:
        values = [
            card.card_id,
            card.title,
            card.status,
            card.summary,
            card.why,
            card.progress_note or "",
            " ".join(card.key_findings),
            " ".join(card.next_actions),
            " ".join(item.label for item in card.inputs),
            " ".join(item.asset_id or "" for item in card.inputs),
            " ".join(item.label for item in card.outputs),
            " ".join(item.role for item in card.outputs),
            " ".join(item.asset_id or "" for item in card.outputs),
        ]
        return cls._normalize_query(" ".join(values))

    @staticmethod
    def _card_asset_ids(card: Card) -> set[str]:
        return {
            item
            for item in [*[input_ref.asset_id for input_ref in card.inputs], *[output.asset_id for output in card.outputs]]
            if item
        }

    @staticmethod
    def _output_contracts_by_asset(cards: list[Card]) -> dict[str, CardOutputSpec]:
        contracts: dict[str, CardOutputSpec] = {}
        for card in cards:
            for output in card.outputs:
                if output.asset_id:
                    contracts[output.asset_id] = output
        return contracts

    @staticmethod
    def _asset_format(path: str | None) -> str | None:
        if not path:
            return None
        lowered = path.lower()
        for suffix in (".tar.gz", ".tsv.gz", ".csv.gz"):
            if lowered.endswith(suffix):
                return suffix[1:]
        suffix = Path(path).suffix.lower().lstrip(".")
        return suffix or None

    @classmethod
    def _compact_asset_record(
        cls,
        record: dict[str, Any],
        materialized_asset,
        contract: CardOutputSpec | None,
    ) -> dict[str, Any]:
        path = materialized_asset.path if materialized_asset else record.get("path")
        asset_format = cls._asset_format(path)
        return {
            "asset_id": record.get("asset_id"),
            "title": materialized_asset.title if materialized_asset else record.get("title"),
            "status": materialized_asset.status if materialized_asset else record.get("status"),
            "asset_type": materialized_asset.asset_type if materialized_asset else record.get("asset_type"),
            "artifact_class": contract.artifact_class if contract else None,
            "role": contract.role if contract else None,
            "accepted_formats": list(contract.accepted_formats) if contract else [],
            "preferred_format": contract.preferred_format if contract else None,
            "format": asset_format,
            "path": path,
            "summary": materialized_asset.summary if materialized_asset else record.get("summary"),
            "producer_card_id": record.get("producer_card_id"),
            "producer_run_id": record.get("producer_run_id"),
            "consumer_card_ids": record.get("consumer_card_ids") or [],
            "materialized": bool(record.get("materialized")),
            "planned": bool(record.get("planned")),
            "step": record.get("step"),
        }

    @classmethod
    def _asset_matches(cls, asset: dict[str, Any], *, query: str, request: FindAssetsPayload) -> bool:
        if request.role and asset.get("role") != request.role:
            return False
        if request.artifact_class and asset.get("artifact_class") != request.artifact_class:
            return False
        if request.format:
            expected = str(request.format).strip().lower().lstrip(".")
            formats = {str(asset.get("format") or "").lower(), *[str(item).lower() for item in asset.get("accepted_formats") or []]}
            if expected not in formats:
                return False
        if request.producer_card_id and asset.get("producer_card_id") != request.producer_card_id:
            return False
        if request.status and asset.get("status") != request.status:
            return False
        if query:
            haystack = cls._normalize_query(
                " ".join(
                    str(asset.get(key) or "")
                    for key in ["asset_id", "title", "status", "asset_type", "artifact_class", "role", "format", "summary", "producer_card_id"]
                )
            )
            if query not in haystack:
                return False
        return True

    @staticmethod
    def _recommended_step(snapshot: dict[str, Any], card: Card) -> int:
        timeline = AssetTimelineService().build(snapshot["project"].project_id, snapshot)
        asset_by_id = {item["asset_id"]: item for item in timeline["assets"]}
        min_step = 1
        for input_ref in card.inputs:
            if not input_ref.asset_id:
                continue
            asset = asset_by_id.get(input_ref.asset_id)
            if asset:
                min_step = max(min_step, int(asset.get("step") or 0) + 1)
        return min_step

    @staticmethod
    def _retry_hint_for_validation_error(message: str) -> str:
        lowered = message.lower()
        if "step too early" in lowered:
            return "Increase card.step to at least the required downstream step."
        if "missing" in lowered and "asset" in lowered:
            return "Use find_assets to choose an existing asset_id, or create an upstream card output first."
        if "duplicate" in lowered:
            return "Choose a unique card_id or output asset_id."
        if "required" in lowered:
            return "Fill the required card fields before calling create_card or update_card."
        return "Inspect the referenced card or asset, then retry with corrected arguments."

    @staticmethod
    def _slugged_template_id(title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40] or "card_template"
        return f"tpl_{slug}_{sha256(f'{title}:{utc_now()}'.encode('utf-8')).hexdigest()[:8]}"

    @staticmethod
    def _sync_module_links(graph, card: Card, previous_card: Card | None) -> None:
        previous_modules = set(previous_card.linked_modules if previous_card else [])
        current_modules = set(card.linked_modules)
        if not previous_modules and not current_modules:
            return
        for module in graph.modules:
            if module.module_id in current_modules and card.card_id not in module.linked_cards:
                module.linked_cards.append(card.card_id)
            if module.module_id in previous_modules and module.module_id not in current_modules:
                module.linked_cards = [item for item in module.linked_cards if item != card.card_id]
        graph.metadata["linked_cards_last_updated"] = card.card_id
        # Callers persist the graph after this helper returns.

    @staticmethod
    def _is_session_upload(asset) -> bool:
        source = str(asset.metadata.get("source") or "")
        if source:
            return source == "manager_chat_upload"
        return asset.path.startswith("data/uploads/")
