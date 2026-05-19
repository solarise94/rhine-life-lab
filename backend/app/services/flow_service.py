from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.models.cards import Card
from app.models.graph import Asset, GraphState, Module, RunRecord
from app.services.project_service import ProjectService


TERMINAL_CARD_STATUSES = {"accepted", "rejected", "cancelled"}
STARTABLE_CARD_STATUSES = {"planned", "failed", "stale", "superseded"}
VALID_INPUT_ASSET_STATUSES = {"valid", "candidate"}


@dataclass(frozen=True)
class CardAssetUse:
    asset_id: str
    label: str


class FlowService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def get_asset_flow(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        cards: list[Card] = snapshot["cards"]
        graph: GraphState = snapshot["graph"]
        asset_map = {asset.asset_id: asset for asset in graph.assets}
        producer_by_asset = self._producer_by_asset(cards, graph.assets, graph.runs)

        card_edges: list[dict] = []
        seen_card_edges: set[tuple[str | None, str, str, str]] = set()
        for card in cards:
            for use in self._required_asset_uses(card, graph.modules, asset_map):
                source_card_id = producer_by_asset.get(use.asset_id)
                if source_card_id == card.card_id:
                    continue
                asset = asset_map.get(use.asset_id)
                edge_type = "card_output_to_input" if source_card_id else "raw_asset_to_card"
                key = (source_card_id, card.card_id, use.asset_id, edge_type)
                if key in seen_card_edges:
                    continue
                seen_card_edges.add(key)
                card_edges.append(
                    {
                        "edge_id": self._edge_id(source_card_id or "raw", card.card_id, use.asset_id, edge_type),
                        "edge_type": edge_type,
                        "source_card_id": source_card_id,
                        "target_card_id": card.card_id,
                        "asset_id": use.asset_id,
                        "asset_title": asset.title if asset else use.label,
                        "asset_status": asset.status if asset else "missing",
                        "label": use.label,
                    }
                )

        asset_edges: list[dict] = []
        seen_asset_edges: set[tuple[str, str]] = set()
        for asset in graph.assets:
            for upstream_asset_id in asset.depends_on:
                key = (upstream_asset_id, asset.asset_id)
                if key in seen_asset_edges:
                    continue
                seen_asset_edges.add(key)
                upstream = asset_map.get(upstream_asset_id)
                asset_edges.append(
                    {
                        "edge_id": self._edge_id(upstream_asset_id, asset.asset_id, "asset_lineage", "asset_lineage"),
                        "edge_type": "asset_lineage",
                        "source_asset_id": upstream_asset_id,
                        "target_asset_id": asset.asset_id,
                        "source_card_id": producer_by_asset.get(upstream_asset_id),
                        "target_card_id": producer_by_asset.get(asset.asset_id),
                        "source_asset_title": upstream.title if upstream else upstream_asset_id,
                        "target_asset_title": asset.title,
                    }
                )

        return {
            "project_id": project_id,
            "cards": [self._card_node(card) for card in cards],
            "assets": [self._asset_node(asset) for asset in graph.assets],
            "card_edges": card_edges,
            "asset_edges": asset_edges,
        }

    def get_work_order(self, project_id: str) -> dict:
        snapshot = self.project_service.get_project_snapshot(project_id)
        cards: list[Card] = snapshot["cards"]
        graph: GraphState = snapshot["graph"]
        asset_map = {asset.asset_id: asset for asset in graph.assets}
        card_map = {card.card_id: card for card in cards}
        producer_by_asset = self._producer_by_asset(cards, graph.assets, graph.runs)
        produced_assets_by_card = self._produced_assets_by_card(cards, graph.assets, graph.runs)
        ordered_card_ids = {card.card_id for card in cards}
        active_card_ids = {card.card_id for card in cards if card.status not in TERMINAL_CARD_STATUSES}

        dependency_map: dict[str, set[str]] = {}
        dependents: dict[str, set[str]] = {card.card_id: set() for card in cards}
        work_items: list[dict] = []

        for card in cards:
            required_uses = self._required_asset_uses(card, graph.modules, asset_map)
            required_asset_ids = [use.asset_id for use in required_uses]
            dependency_ids = {
                producer
                for asset_id in required_asset_ids
                if (producer := producer_by_asset.get(asset_id)) and producer != card.card_id
            }
            dependency_map[card.card_id] = dependency_ids
            for dependency_id in dependency_ids:
                dependents.setdefault(dependency_id, set()).add(card.card_id)

            missing_asset_ids = [asset_id for asset_id in required_asset_ids if asset_id not in asset_map]
            nonvalid_asset_ids = [
                asset_id
                for asset_id in required_asset_ids
                if asset_id in asset_map and asset_map[asset_id].status not in VALID_INPUT_ASSET_STATUSES
            ]
            unmet_dependency_ids = [
                dep_id
                for dep_id in sorted(dependency_ids)
                if dep_id in card_map and card_map[dep_id].status != "accepted"
            ]
            can_start = (
                card.status in STARTABLE_CARD_STATUSES
                and not missing_asset_ids
                and not nonvalid_asset_ids
                and not unmet_dependency_ids
            )
            block_reasons = self._block_reasons(card, missing_asset_ids, nonvalid_asset_ids, unmet_dependency_ids)

            work_items.append(
                {
                    "card_id": card.card_id,
                    "title": card.title,
                    "status": card.status,
                    "card_type": card.card_type,
                    "required_asset_ids": required_asset_ids,
                    "produced_asset_ids": produced_assets_by_card.get(card.card_id, []),
                    "depends_on_card_ids": sorted(dependency_ids),
                    "blocked_by_card_ids": unmet_dependency_ids,
                    "blocked_by_asset_ids": missing_asset_ids + nonvalid_asset_ids,
                    "can_start": can_start,
                    "block_reasons": block_reasons,
                    "active": card.card_id in active_card_ids,
                }
            )

        parallel_batches, cycle_card_ids = self._parallel_batches(ordered_card_ids, dependency_map)
        return {
            "project_id": project_id,
            "work_items": work_items,
            "parallel_batches": parallel_batches,
            "dependency_edges": [
                {
                    "edge_id": self._edge_id(source_id, target_id, "work_dependency", "work_dependency"),
                    "source_card_id": source_id,
                    "target_card_id": target_id,
                    "edge_type": "work_dependency",
                }
                for target_id, source_ids in sorted(dependency_map.items())
                for source_id in sorted(source_ids)
            ],
            "cycle_card_ids": cycle_card_ids,
        }

    @staticmethod
    def _producer_by_asset(cards: list[Card], assets: list[Asset], runs: list[RunRecord]) -> dict[str, str]:
        run_card_by_id = {run.run_id: run.card_id for run in runs}
        producer_by_asset: dict[str, str] = {}
        for asset in assets:
            if asset.created_by_run and asset.created_by_run in run_card_by_id:
                producer_by_asset[asset.asset_id] = run_card_by_id[asset.created_by_run]
        for card in cards:
            for output in card.outputs:
                if output.asset_id:
                    producer_by_asset[output.asset_id] = card.card_id
        return producer_by_asset

    @staticmethod
    def _produced_assets_by_card(cards: list[Card], assets: list[Asset], runs: list[RunRecord]) -> dict[str, list[str]]:
        run_card_by_id = {run.run_id: run.card_id for run in runs}
        produced: dict[str, list[str]] = {card.card_id: [] for card in cards}
        for asset in assets:
            if asset.created_by_run and asset.created_by_run in run_card_by_id:
                FlowService._append_unique(produced.setdefault(run_card_by_id[asset.created_by_run], []), asset.asset_id)
        for card in cards:
            for output in card.outputs:
                if output.asset_id:
                    FlowService._append_unique(produced.setdefault(card.card_id, []), output.asset_id)
        return produced

    @staticmethod
    def _required_asset_uses(card: Card, modules: list[Module], asset_map: dict[str, Asset]) -> list[CardAssetUse]:
        uses: list[CardAssetUse] = []
        seen: set[str] = set()
        for item in card.inputs:
            if item.asset_id and item.asset_id not in seen:
                seen.add(item.asset_id)
                uses.append(CardAssetUse(asset_id=item.asset_id, label=item.label))
        module_map = {module.module_id: module for module in modules}
        for module_id in card.linked_modules:
            module = module_map.get(module_id)
            if not module:
                continue
            for asset_id in module.depends_on_assets:
                if asset_id in seen:
                    continue
                seen.add(asset_id)
                asset = asset_map.get(asset_id)
                uses.append(CardAssetUse(asset_id=asset_id, label=asset.title if asset else asset_id))
        return uses

    @staticmethod
    def _parallel_batches(active_card_ids: set[str], dependency_map: dict[str, set[str]]) -> tuple[list[dict], list[str]]:
        active_dependencies = {
            card_id: {dep_id for dep_id in deps if dep_id in active_card_ids}
            for card_id, deps in dependency_map.items()
            if card_id in active_card_ids
        }
        remaining = {card_id: set(deps) for card_id, deps in active_dependencies.items()}
        ready = deque(sorted(card_id for card_id, deps in remaining.items() if not deps))
        batches: list[dict] = []
        scheduled: set[str] = set()

        while ready:
            batch_ids = list(ready)
            ready.clear()
            batches.append({"batch_index": len(batches), "card_ids": batch_ids})
            scheduled.update(batch_ids)
            for card_id in batch_ids:
                for target_id, deps in remaining.items():
                    deps.discard(card_id)
                    if target_id not in scheduled and not deps and target_id not in ready:
                        ready.append(target_id)

        cycle_card_ids = sorted(active_card_ids - scheduled)
        return batches, cycle_card_ids

    @staticmethod
    def _block_reasons(
        card: Card,
        missing_asset_ids: list[str],
        nonvalid_asset_ids: list[str],
        unmet_dependency_ids: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        if card.status == "proposed":
            reasons.append("proposal_not_accepted")
        elif card.status in TERMINAL_CARD_STATUSES:
            reasons.append(f"terminal_status:{card.status}")
        elif card.status not in STARTABLE_CARD_STATUSES:
            reasons.append(f"not_startable_status:{card.status}")
        if missing_asset_ids:
            reasons.append("missing_required_assets")
        if nonvalid_asset_ids:
            reasons.append("required_assets_not_valid")
        if unmet_dependency_ids:
            reasons.append("upstream_cards_not_accepted")
        return reasons

    @staticmethod
    def _card_node(card: Card) -> dict:
        return {
            "card_id": card.card_id,
            "title": card.title,
            "status": card.status,
            "card_type": card.card_type,
            "linked_modules": card.linked_modules,
        }

    @staticmethod
    def _asset_node(asset: Asset) -> dict:
        return {
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type,
            "title": asset.title,
            "status": asset.status,
            "created_by_run": asset.created_by_run,
            "depends_on": asset.depends_on,
            "summary": asset.summary,
            "path": asset.path,
        }

    @staticmethod
    def _append_unique(items: list[str], value: str) -> None:
        if value not in items:
            items.append(value)

    @staticmethod
    def _edge_id(source_id: str, target_id: str, asset_id: str, edge_type: str) -> str:
        return f"{edge_type}:{source_id}:{target_id}:{asset_id}"
