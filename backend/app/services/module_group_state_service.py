from __future__ import annotations

from app.models.cards import AggregateStatus, Card, CardStatus
from app.models.graph import Module


class ModuleGroupStateService:
    @staticmethod
    def sync_linked_module_status_from_card(card: Card, modules: list[Module]) -> None:
        for module in modules:
            if module.module_id not in card.linked_modules:
                continue
            if module.type == "module_group" and module.submodules:
                continue
            module.status = card.status

    @staticmethod
    def sync_linked_card_status_from_module(module: Module, cards: list[Card]) -> None:
        if module.type == "module_group" and module.submodules:
            return
        for card in cards:
            if module.module_id in card.linked_modules:
                card.status = module.status

    @classmethod
    def sync_group_hierarchy(cls, cards: list[Card], modules: list[Module]) -> None:
        module_by_id = {module.module_id: module for module in modules}
        for group in [item for item in modules if item.type == "module_group"]:
            child_statuses: list[CardStatus] = []
            for ref in group.submodules:
                child = module_by_id.get(ref.module_id)
                if child is not None:
                    ref.title = child.title
                    ref.status = child.status
                child_statuses.append(ref.status)
            if child_statuses:
                group_status, _aggregate = cls._derive_group_status(child_statuses)
                group.status = group_status

        for card in [item for item in cards if item.card_type == "module_group"]:
            if card.status in {"proposed", "cancelled", "rejected", "superseded"}:
                continue
            if card.linked_runs:
                continue
            group = next(
                (
                    module_by_id[module_id]
                    for module_id in card.linked_modules
                    if module_id in module_by_id and module_by_id[module_id].type == "module_group"
                ),
                None,
            )
            if not group or not group.submodules:
                continue
            child_statuses = [ref.status for ref in group.submodules]
            card.status, card.aggregate_status = cls._derive_group_status(child_statuses)

    @staticmethod
    def _derive_group_status(child_statuses: list[CardStatus]) -> tuple[CardStatus, AggregateStatus]:
        if all(status == "accepted" for status in child_statuses):
            return "accepted", "all_accepted"
        if any(status in {"running", "reviewing"} for status in child_statuses):
            return "running", "has_running"
        if any(status == "needs_review" for status in child_statuses):
            return "needs_review", "mixed"
        if any(status == "failed" for status in child_statuses):
            return "failed", "has_failed"
        if any(status in {"stale", "superseded"} for status in child_statuses):
            return "stale", "stale"
        if any(status in {"planned", "proposed", "cancelled", "rejected"} for status in child_statuses):
            return "planned", "partially_planned"
        return "planned", "mixed"
