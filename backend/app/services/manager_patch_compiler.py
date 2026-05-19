from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.cards import Card
from app.models.graph import Asset, GraphState, Module
from app.models.patches import GraphPatch, PatchOp
from app.services.manager_planner import ManagerPlanningError


@dataclass
class ManagerPatchCompiler:
    cards: dict[str, Card]
    modules: dict[str, Module]
    assets: dict[str, Asset]

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "ManagerPatchCompiler":
        cards = {card.card_id: card for card in snapshot["cards"]}
        modules = {module.module_id: module for module in snapshot["graph"].modules}
        assets = {asset.asset_id: asset for asset in snapshot["graph"].assets}
        return cls(cards=cards, modules=modules, assets=assets)

    def normalize_patch(self, patch: GraphPatch) -> GraphPatch:
        created_modules: dict[str, str] = {}
        normalized_ops: list[PatchOp] = []

        for op in patch.ops:
            payload = dict(op.payload)

            if op.op in {"create_module", "create_module_group"}:
                module_id = payload.get("module_id")
                title = payload.get("title")
                if payload.get("status") == "proposed":
                    payload["status"] = "planned"
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

        normalized_ops = self._wire_planned_asset_dependencies(normalized_ops)
        return patch.model_copy(update={"ops": normalized_ops})

    def _wire_planned_asset_dependencies(self, ops: list[PatchOp]) -> list[PatchOp]:
        prospective_cards: dict[str, dict] = {}
        prospective_modules: dict[str, dict] = {}
        card_op_indexes: dict[int, str] = {}
        module_op_indexes: dict[int, str] = {}

        for index, op in enumerate(ops):
            if op.op in {"create_card", "update_card"}:
                payload = self._merge_card_payload(op)
                prospective_cards[payload["card_id"]] = payload
                card_op_indexes[index] = payload["card_id"]
            elif op.op in {"create_module", "create_module_group", "update_module"}:
                payload = self._merge_module_payload(op)
                prospective_modules[payload["module_id"]] = payload
                module_op_indexes[index] = payload["module_id"]

        used_asset_ids = set(self.assets)
        for payload in prospective_cards.values():
            outputs = payload.get("outputs") or []
            for output in outputs:
                if output.get("asset_id"):
                    used_asset_ids.add(output["asset_id"])
            payload["outputs"] = self._assign_output_asset_ids(payload["card_id"], outputs, used_asset_ids)

        providers = self._build_asset_providers(prospective_cards)
        for payload in prospective_cards.values():
            payload["inputs"] = self._resolve_card_inputs(payload["card_id"], payload.get("inputs") or [], providers)
            input_asset_ids = [item["asset_id"] for item in payload["inputs"] if item.get("asset_id")]
            if input_asset_ids:
                payload["linked_assets"] = self._merge_unique(payload.get("linked_assets") or [], input_asset_ids)

        module_to_cards = self._module_to_cards(prospective_modules, prospective_cards)
        for module_id, payload in prospective_modules.items():
            linked_cards = module_to_cards.get(module_id, [])
            if linked_cards:
                payload["linked_cards"] = linked_cards
            inferred_dependencies: list[str] = []
            for card_id in linked_cards:
                card_payload = prospective_cards.get(card_id)
                if not card_payload:
                    existing = self.cards.get(card_id)
                    card_payload = existing.model_dump() if existing else None
                if not card_payload:
                    continue
                inferred_dependencies = self._merge_unique(
                    inferred_dependencies,
                    [item["asset_id"] for item in card_payload.get("inputs", []) if item.get("asset_id")],
                )
            if inferred_dependencies:
                payload["depends_on_assets"] = inferred_dependencies

        rewritten_ops: list[PatchOp] = []
        for index, op in enumerate(ops):
            if index in card_op_indexes:
                rewritten_ops.append(PatchOp(op=op.op, payload=prospective_cards[card_op_indexes[index]]))
            elif index in module_op_indexes:
                rewritten_ops.append(PatchOp(op=op.op, payload=prospective_modules[module_op_indexes[index]]))
            else:
                rewritten_ops.append(op)
        return rewritten_ops

    def _build_asset_providers(self, prospective_cards: dict[str, dict]) -> list[dict]:
        providers: list[dict] = []
        order = 0
        for card_id, card in self.cards.items():
            if card_id in prospective_cards:
                continue
            for output in card.outputs:
                if not output.asset_id:
                    continue
                providers.append(self._provider_entry(card_id, output.label, output.asset_id, priority=0, order=order))
                order += 1

        for card_id, payload in prospective_cards.items():
            for output in payload.get("outputs") or []:
                if not output.get("asset_id"):
                    continue
                providers.append(self._provider_entry(card_id, output.get("label", ""), output["asset_id"], priority=100, order=order))
                order += 1
        return providers

    def _provider_entry(self, card_id: str, label: str, asset_id: str, priority: int, order: int) -> dict:
        semantic = self._semantic_key(label)
        return {
            "card_id": card_id,
            "label": label,
            "asset_id": asset_id,
            "priority": priority,
            "order": order,
            "normalized_label": self._normalize_label(label),
            "semantic": semantic,
            "family": self._semantic_family(semantic),
        }

    def _resolve_card_inputs(self, card_id: str, inputs: list[dict], providers: list[dict]) -> list[dict]:
        resolved: list[dict] = []
        for item in inputs:
            entry = dict(item)
            if not entry.get("asset_id"):
                asset_id = self._resolve_input_asset_id(card_id, entry.get("label", ""), providers)
                if asset_id:
                    entry["asset_id"] = asset_id
            resolved.append(entry)
        return resolved

    def _resolve_input_asset_id(self, card_id: str, label: str, providers: list[dict]) -> str | None:
        normalized = self._normalize_label(label)
        semantic = self._semantic_key(label)
        family = self._semantic_family(semantic)
        candidates: list[tuple[int, int, int, str]] = []

        for provider in providers:
            if provider["card_id"] == card_id:
                continue
            score = 0
            if normalized and provider["normalized_label"] == normalized:
                score = 300
            elif semantic and provider["semantic"] == semantic:
                score = 240
            elif family and provider["family"] == family:
                score = 180
            elif normalized and provider["normalized_label"] and (
                normalized in provider["normalized_label"] or provider["normalized_label"] in normalized
            ):
                score = 120
            if not score:
                continue
            candidates.append((score + provider["priority"], provider["priority"], -provider["order"], provider["asset_id"]))

        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][3]

    def _assign_output_asset_ids(self, card_id: str, outputs: list[dict], used_asset_ids: set[str]) -> list[dict]:
        assigned: list[dict] = []
        for index, item in enumerate(outputs):
            entry = dict(item)
            if not entry.get("asset_id"):
                entry["asset_id"] = self._planned_asset_id(card_id, entry.get("label", ""), used_asset_ids, index)
            used_asset_ids.add(entry["asset_id"])
            assigned.append(entry)
        return assigned

    def _planned_asset_id(self, card_id: str, label: str, used_asset_ids: set[str], index: int) -> str:
        card_slug = card_id[5:] if card_id.startswith("card_") else card_id
        label_slug = self._semantic_key(label) or self._slugify(label) or f"output_{index + 1}"
        candidate = f"asset_{card_slug}_{label_slug}"
        suffix = 2
        while candidate in used_asset_ids:
            candidate = f"asset_{card_slug}_{label_slug}_{suffix}"
            suffix += 1
        return candidate

    def _module_to_cards(self, prospective_modules: dict[str, dict], prospective_cards: dict[str, dict]) -> dict[str, list[str]]:
        module_to_cards: dict[str, list[str]] = {}
        for module_id, module in prospective_modules.items():
            module_to_cards[module_id] = list(module.get("linked_cards") or [])
        for module_id, module in self.modules.items():
            module_to_cards.setdefault(module_id, list(module.linked_cards))
        for card_id, payload in prospective_cards.items():
            for module_id in payload.get("linked_modules") or []:
                module_to_cards[module_id] = self._merge_unique(module_to_cards.get(module_id, []), [card_id])
        return module_to_cards

    def _merge_card_payload(self, op: PatchOp) -> dict:
        if op.op == "create_card":
            payload = dict(op.payload)
            if payload.get("status") == "proposed":
                payload["status"] = "planned"
            payload.setdefault("why", "")
            payload.setdefault("inputs", [])
            payload.setdefault("outputs", [])
            payload.setdefault("key_findings", [])
            payload.setdefault("manager_review", "")
            payload.setdefault("next_actions", [])
            payload.setdefault("linked_modules", [])
            payload.setdefault("linked_runs", [])
            payload.setdefault("linked_assets", [])
            payload.setdefault("progress_note", None)
            return payload

        card_id = op.payload.get("card_id")
        if not card_id:
            raise ManagerPlanningError("update_card requires card_id.")
        existing_card = self.cards.get(card_id)
        if not existing_card:
            raise ManagerPlanningError(f"update_card references unknown card: {card_id}")
        existing = existing_card.model_dump()
        merged = {
            "card_id": existing["card_id"],
            "title": existing["title"],
            "status": existing["status"],
            "summary": existing["summary"],
            "why": existing["why"],
            "inputs": [dict(item) for item in existing["inputs"]],
            "outputs": [dict(item) for item in existing["outputs"]],
            "key_findings": list(existing["key_findings"]),
            "manager_review": existing["manager_review"],
            "next_actions": list(existing["next_actions"]),
            "linked_modules": list(existing["linked_modules"]),
            "linked_assets": list(existing["linked_assets"]),
            "progress_note": existing["progress_note"],
        }
        merged.update(dict(op.payload))
        return merged

    def _merge_module_payload(self, op: PatchOp) -> dict:
        if op.op in {"create_module", "create_module_group"}:
            payload = dict(op.payload)
            payload.setdefault("status", "planned")
            payload.setdefault("summary", "")
            payload.setdefault("depends_on_assets", [])
            payload.setdefault("expected_outputs", [])
            payload.setdefault("linked_cards", [])
            return payload

        module_id = op.payload.get("module_id")
        if not module_id:
            raise ManagerPlanningError("update_module requires module_id.")
        existing_module = self.modules.get(module_id)
        if not existing_module:
            raise ManagerPlanningError(f"update_module references unknown module: {module_id}")
        existing = existing_module
        merged = {
            "module_id": existing.module_id,
            "title": existing.title,
            "status": existing.status,
            "summary": existing.summary,
            "depends_on_assets": list(existing.depends_on_assets),
            "expected_outputs": list(existing.expected_outputs),
            "linked_cards": list(existing.linked_cards),
        }
        merged.update(dict(op.payload))
        return merged

    @staticmethod
    def _merge_unique(existing: list[str], additions: list[str]) -> list[str]:
        merged = list(existing)
        for item in additions:
            if item and item not in merged:
                merged.append(item)
        return merged

    @staticmethod
    def _normalize_label(label: str) -> str:
        lowered = label.strip().lower()
        return re.sub(r"[\s\-_()（）\[\]【】{}:：,，./\\\\]+", "", lowered)

    def _semantic_key(self, label: str) -> str:
        lowered = label.strip().lower()
        normalized = self._normalize_label(label)

        if ("标准化" in label and "矩阵" in label) or ("normalized" in lowered and "matrix" in lowered):
            return "normalized_matrix"
        if "metadata" in lowered or "元数据" in label or "样本信息" in label:
            return "sample_metadata"
        if "deg" in lowered or "差异表达结果" in label:
            return "deg_table"
        if "volcano" in lowered or "火山" in label:
            return "volcano_plot"
        if normalized == "ma图" or "maplot" in normalized or ("ma" in lowered and "图" in label):
            return "ma_plot"
        if "heatmap" in lowered or "热图" in label:
            return "heatmap"
        if "pca" in lowered and ("得分" in label or "score" in lowered):
            return "pca_scores"
        if "pca" in lowered and ("散点" in label or "scatter" in lowered):
            return "pca_scatter"
        if ("方差" in label and "解释" in label) or "variance" in lowered:
            return "pca_variance"
        if "pca" in lowered:
            return "pca_result"
        if "go" in lowered and ("网络" in label or "net" in lowered):
            return "go_netplot"
        if "go" in lowered and ("条形" in label or "bar" in lowered):
            return "go_barplot"
        if "go" in lowered and ("气泡" in label or "dot" in lowered):
            return "go_dotplot"
        if "go" in lowered:
            return "go_result"
        if "kegg" in lowered and ("通路图" in label or "pathway" in lowered):
            return "kegg_pathway_plot"
        if "kegg" in lowered and ("气泡" in label or "dot" in lowered):
            return "kegg_dotplot"
        if "kegg" in lowered:
            return "kegg_result"
        if "report" in lowered or "报告" in label:
            return "report"
        if "计数矩阵" in label or "countmatrix" in normalized:
            return "count_matrix"
        return self._slugify(label)

    @staticmethod
    def _semantic_family(semantic: str) -> str:
        if semantic.startswith("pca_") or semantic == "pca_result":
            return "pca"
        if semantic.startswith("go_") or semantic == "go_result":
            return "go"
        if semantic.startswith("kegg_") or semantic == "kegg_result":
            return "kegg"
        if semantic.endswith("_matrix") or semantic == "count_matrix":
            return "matrix"
        if semantic == "sample_metadata":
            return "metadata"
        if semantic == "deg_table":
            return "deg"
        if semantic == "report":
            return "report"
        return semantic

    @staticmethod
    def _slugify(text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
        return slug[:48]


class ModulePlaceholder:
    def __init__(self, module_id: str) -> None:
        self.module_id = module_id
        self.title = module_id.replace("_", " ")
