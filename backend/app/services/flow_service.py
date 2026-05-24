from __future__ import annotations

from app.models.cards import Card
from app.models.graph import Asset, GraphState, Module, RunRecord
from app.services.asset_timeline_service import AssetTimelineService
from app.services.project_service import ProjectService


class FlowService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service
        self.timeline_service = AssetTimelineService()

    def get_asset_flow(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        timeline = self.timeline_service.build(project_id, snapshot)
        return {
            "project_id": project_id,
            "cards": timeline["cards"],
            "assets": timeline["assets"],
            "card_edges": self._card_edges(snapshot["cards"], snapshot["graph"], timeline["producer_by_asset"]),
            "asset_edges": self._asset_edges(snapshot["graph"].assets, timeline["producer_by_asset"]),
            "timeline": timeline,
        }

    def get_work_order(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph: GraphState = snapshot["graph"]
        timeline = self.timeline_service.build(project_id, snapshot)
        asset_map = {asset.asset_id: asset for asset in graph.assets}
        card_map = {card.card_id: card for card in snapshot["cards"]}
        work_items: list[dict] = []

        for card in snapshot["cards"]:
            required_uses = timeline["required_uses_by_card"].get(card.card_id, [])
            required_asset_ids = [use.asset_id for use in required_uses]
            dependency_ids = sorted(timeline["dependency_map"].get(card.card_id, set()))
            missing_asset_ids = [
                asset_id
                for asset_id in required_asset_ids
                if asset_id not in asset_map and asset_id not in timeline["producer_by_asset"]
            ]
            nonvalid_asset_ids = [
                asset_id
                for asset_id in required_asset_ids
                if asset_id in asset_map and asset_map[asset_id].status not in {"valid", "candidate"}
            ]
            unmet_dependency_ids = [
                dep_id
                for dep_id in dependency_ids
                if dep_id in card_map and card_map[dep_id].status != "accepted"
            ]
            missing_script_binding_ids = self._missing_script_asset_binding_ids(card)
            can_start = (
                card.status in {"planned", "failed", "stale", "superseded"}
                and not missing_asset_ids
                and not nonvalid_asset_ids
                and not unmet_dependency_ids
                and not missing_script_binding_ids
            )
            block_reasons = self._block_reasons(
                card,
                missing_asset_ids,
                nonvalid_asset_ids,
                unmet_dependency_ids,
                missing_script_binding_ids,
            )
            work_items.append(
                {
                    "card_id": card.card_id,
                    "title": card.title,
                    "status": card.status,
                    "card_type": card.card_type,
                    "step": card.step or 1,
                    "required_asset_ids": required_asset_ids,
                    "produced_asset_ids": timeline["produced_assets_by_card"].get(card.card_id, []),
                    "depends_on_card_ids": dependency_ids,
                    "blocked_by_card_ids": unmet_dependency_ids,
                    "blocked_by_asset_ids": missing_asset_ids + nonvalid_asset_ids,
                    "missing_script_asset_requirement_ids": missing_script_binding_ids,
                    "planned_input_asset_ids": [
                        asset_id for asset_id in required_asset_ids if asset_id not in asset_map and asset_id in timeline["producer_by_asset"]
                    ],
                    "can_start": can_start,
                    "block_reasons": block_reasons,
                    "active": card.status not in {"accepted", "rejected", "cancelled"},
                }
            )

        return {
            "project_id": project_id,
            "work_items": work_items,
            "parallel_batches": timeline["parallel_batches"],
            "dependency_edges": [
                {
                    "edge_id": self._edge_id(source_id, target_id, "work_dependency", "work_dependency"),
                    "source_card_id": source_id,
                    "target_card_id": target_id,
                    "edge_type": "work_dependency",
                }
                for target_id, source_ids in sorted(timeline["dependency_map"].items())
                for source_id in sorted(source_ids)
            ],
            "cycle_card_ids": timeline["cycle_card_ids"],
        }

    def get_timeline(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        return self.timeline_service.build(project_id, snapshot)

    @staticmethod
    def _card_edges(cards: list[Card], graph: GraphState, producer_by_asset: dict[str, str]) -> list[dict]:
        asset_map = {asset.asset_id: asset for asset in graph.assets}
        card_edges: list[dict] = []
        seen_card_edges: set[tuple[str | None, str, str, str]] = set()
        for card in cards:
            for item in card.inputs:
                if not item.asset_id:
                    continue
                source_card_id = producer_by_asset.get(item.asset_id)
                if source_card_id == card.card_id:
                    continue
                asset = asset_map.get(item.asset_id)
                edge_type = "card_output_to_input" if source_card_id else "raw_asset_to_card"
                key = (source_card_id, card.card_id, item.asset_id, edge_type)
                if key in seen_card_edges:
                    continue
                seen_card_edges.add(key)
                card_edges.append(
                    {
                        "edge_id": FlowService._edge_id(source_card_id or "raw", card.card_id, item.asset_id, edge_type),
                        "edge_type": edge_type,
                        "source_card_id": source_card_id,
                        "target_card_id": card.card_id,
                        "asset_id": item.asset_id,
                        "asset_title": asset.title if asset else item.label,
                        "asset_status": asset.status if asset else "missing",
                        "label": item.label,
                    }
                )
        return card_edges

    @staticmethod
    def _asset_edges(assets: list[Asset], producer_by_asset: dict[str, str]) -> list[dict]:
        asset_map = {asset.asset_id: asset for asset in assets}
        asset_edges: list[dict] = []
        seen_asset_edges: set[tuple[str, str]] = set()
        for asset in assets:
            for upstream_asset_id in asset.depends_on:
                key = (upstream_asset_id, asset.asset_id)
                if key in seen_asset_edges:
                    continue
                seen_asset_edges.add(key)
                upstream = asset_map.get(upstream_asset_id)
                asset_edges.append(
                    {
                        "edge_id": FlowService._edge_id(upstream_asset_id, asset.asset_id, "asset_lineage", "asset_lineage"),
                        "edge_type": "asset_lineage",
                        "source_asset_id": upstream_asset_id,
                        "target_asset_id": asset.asset_id,
                        "source_card_id": producer_by_asset.get(upstream_asset_id),
                        "target_card_id": producer_by_asset.get(asset.asset_id),
                        "source_asset_title": upstream.title if upstream else upstream_asset_id,
                        "target_asset_title": asset.title,
                    }
                )
        return asset_edges

    @staticmethod
    def _block_reasons(
        card: Card,
        missing_asset_ids: list[str],
        nonvalid_asset_ids: list[str],
        unmet_dependency_ids: list[str],
        missing_script_binding_ids: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        if card.status == "proposed":
            reasons.append("proposal_not_accepted")
        elif card.status in {"accepted", "rejected", "cancelled"}:
            reasons.append(f"terminal_status:{card.status}")
        elif card.status not in {"planned", "failed", "stale", "superseded"}:
            reasons.append(f"not_startable_status:{card.status}")
        if missing_asset_ids:
            reasons.append("missing_required_assets")
        if nonvalid_asset_ids:
            reasons.append("required_assets_not_valid")
        if unmet_dependency_ids:
            reasons.append("upstream_cards_not_accepted")
        if missing_script_binding_ids:
            reasons.append("missing_script_asset_bindings")
        return reasons

    @staticmethod
    def _missing_script_asset_binding_ids(card: Card) -> list[str]:
        context = card.executor_context
        if context is None:
            return []
        bound_ids = {
            binding.requirement_id
            for binding in context.script_asset_bindings
            if binding.requirement_id and (binding.asset_id or binding.path)
        }
        return [
            requirement.requirement_id
            for requirement in context.script_asset_requirements
            if requirement.requirement_id and not requirement.optional and requirement.requirement_id not in bound_ids
        ]

    @staticmethod
    def _edge_id(source_id: str, target_id: str, asset_id: str, edge_type: str) -> str:
        return f"{edge_type}:{source_id}:{target_id}:{asset_id}"
