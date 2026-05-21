from __future__ import annotations

from app.models.cards import Card, CardAssetRef
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
