from __future__ import annotations

from dataclasses import dataclass

from app.models.cards import Card
from app.models.graph import GraphState, Module
from app.models.patches import GraphPatch, PatchOp


@dataclass
class ManagerPatchCompiler:
    cards: dict[str, Card]
    modules: dict[str, Module]

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "ManagerPatchCompiler":
        cards = {card.card_id: card for card in snapshot["cards"]}
        modules = {module.module_id: module for module in snapshot["graph"].modules}
        return cls(cards=cards, modules=modules)

    def normalize_patch(self, patch: GraphPatch) -> GraphPatch:
        created_modules: dict[str, str] = {}
        normalized_ops: list[PatchOp] = []

        for op in patch.ops:
            payload = dict(op.payload)

            if op.op in {"create_module", "create_module_group"}:
                module_id = payload.get("module_id")
                title = payload.get("title")
                if module_id and title:
                    created_modules[module_id] = title
                normalized_ops.append(PatchOp(op=op.op, payload=payload))
                continue

            if op.op == "add_submodule":
                child_id = payload.get("module_id") or payload.get("child_module_id")
                if child_id:
                    payload["module_id"] = child_id
                if not payload.get("title") and child_id:
                    payload["title"] = created_modules.get(child_id) or self.modules.get(child_id, ModulePlaceholder(child_id)).title
                payload.pop("child_module_id", None)
                normalized_ops.append(PatchOp(op=op.op, payload=payload))
                continue

            if op.op == "update_card":
                target_id = payload.get("card_id")
                if target_id and target_id not in self.cards and target_id in self.modules:
                    module_payload = {"module_id": target_id}
                    for key in ("title", "status", "summary", "depends_on_assets", "expected_outputs", "linked_cards"):
                        if key in payload:
                            module_payload[key] = payload[key]
                    normalized_ops.append(PatchOp(op="update_module", payload=module_payload))
                    continue
                normalized_ops.append(PatchOp(op=op.op, payload=payload))
                continue

            if op.op == "create_run":
                payload.setdefault("status", "queued")
                payload.setdefault("title", f"{payload.get('card_id', 'unknown')} 执行")
                payload.setdefault("summary", "等待执行。")
                payload.setdefault("started_at", "")
                normalized_ops.append(PatchOp(op=op.op, payload=payload))
                continue

            if op.op == "mark_downstream_stale" and "asset_ids" not in payload:
                trigger_asset_ids = payload.get("trigger_asset_ids")
                if isinstance(trigger_asset_ids, list):
                    payload["asset_ids"] = trigger_asset_ids
                normalized_ops.append(PatchOp(op=op.op, payload=payload))
                continue

            normalized_ops.append(PatchOp(op=op.op, payload=payload))

        return patch.model_copy(update={"ops": normalized_ops})


class ModulePlaceholder:
    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        self.title = module_id.replace("_", " ")
