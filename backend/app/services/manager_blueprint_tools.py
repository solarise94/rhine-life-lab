from __future__ import annotations

from hashlib import sha256
import re

from app.models.executor import ExecutorContext
from app.models.cards import Card, CardAssetRef
from app.models.memory import ProjectMemoryItem
from app.services.asset_timeline_service import AssetTimelineService
from app.services.manager_planner import ManagerPlanningError
from app.services.module_group_state_service import ModuleGroupStateService
from app.services.project_service import ProjectService
from app.services.result_asset_service import ResultAssetService
from app.services.utils import utc_now


class ManagerBlueprintTools:
    """Controlled tools exposed to the external manager agent runtime."""

    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service
        self.result_asset_service = ResultAssetService(project_service)
        self.asset_timeline_service = AssetTimelineService()

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
        return {"card": card.model_dump(), "timeline": self.asset_timeline_service.build(project_id, self.project_service.get_project_snapshot(project_id))}

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
            ModuleGroupStateService.sync_linked_module_status_from_card(updated, graph.modules)
            ModuleGroupStateService.sync_group_hierarchy(cards, graph.modules)
            store.save_graph(graph)
            store.save_cards(cards)
            self._audit_card_tool(project_id, "update_card", card_id, payload)
        return {"card": updated.model_dump(), "timeline": self.asset_timeline_service.build(project_id, self.project_service.get_project_snapshot(project_id))}

    def configure_card_execution(self, project_id: str, payload: dict) -> dict:
        card_ids = [str(item).strip() for item in payload.get("card_ids") or [] if str(item).strip()]
        if not card_ids:
            card_id = str(payload.get("card_id") or "").strip()
            if card_id:
                card_ids = [card_id]
        if not card_ids:
            raise ManagerPlanningError("configure_card_execution requires card_id or card_ids.")
        tool_policy = payload.get("tool_policy") if isinstance(payload.get("tool_policy"), dict) else {}
        runtime_bindings = payload.get("runtime_bindings") if isinstance(payload.get("runtime_bindings"), dict) else {}
        instruction_blocks = payload.get("instruction_blocks") if isinstance(payload.get("instruction_blocks"), list) else None
        allowed_network = {None, "allow", "deny", "prompt"}
        if tool_policy.get("network") not in allowed_network:
            raise ManagerPlanningError("tool_policy.network must be allow, deny, or prompt.")
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
                card.progress_note = str(payload.get("progress_note") or card.progress_note or "").strip() or card.progress_note
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
        return {"card": updated.model_dump(), "timeline": self.asset_timeline_service.build(project_id, self.project_service.get_project_snapshot(project_id))}

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
        outputs = [item if isinstance(item, CardAssetRef) else CardAssetRef.model_validate(item) for item in payload.get("outputs") or []]
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
