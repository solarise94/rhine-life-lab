from __future__ import annotations

from collections import defaultdict

from pydantic import ValidationError

from app.models.cards import Card
from app.models.graph import Asset, Claim, ReportItem, RunRecord
from app.models.patches import GraphPatch, ValidationResult
from app.services.project_service import ProjectService


ALLOWLIST = {
    "create_module",
    "update_module_summary",
    "set_module_status",
    "create_module_group",
    "add_submodule",
    "create_card",
    "update_card",
    "set_card_status",
    "create_asset",
    "set_asset_status",
    "connect_dependency",
    "create_claim",
    "set_claim_status",
    "create_run",
    "attach_run_to_card",
    "attach_asset_to_card",
    "add_report_item",
    "remove_report_item",
    "mark_downstream_stale",
    "propose_cleanup",
    "semantic_rollback",
}

CARD_READONLY_FIELDS = {"card_id", "linked_runs", "technical_refs"}
ASSET_READONLY_FIELDS = {"asset_id", "artifact_id"}


class PatchValidator:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def validate_patch(self, project_id: str, patch: GraphPatch) -> ValidationResult:
        store = self.project_service.graph_store(project_id)
        graph = store.load_graph()
        cards = store.load_cards()

        module_ids = {module.module_id for module in graph.modules}
        asset_ids = {asset.asset_id for asset in graph.assets}
        card_ids = {card.card_id for card in cards}
        module_child_edges = {module.module_id: {item.module_id for item in module.submodules} for module in graph.modules}
        asset_edges = {asset.asset_id: set(asset.depends_on) for asset in graph.assets}
        errors: list[str] = []
        warnings: list[str] = []
        new_module_ids: set[str] = set()
        new_asset_ids: set[str] = set()
        new_card_ids: set[str] = set()

        try:
            GraphPatch.model_validate(patch.model_dump())
        except ValidationError as exc:
            errors.append(f"Patch schema validation failed: {exc}")
            return ValidationResult(valid=False, errors=errors, warnings=warnings)

        for op in patch.ops:
            if op.op not in ALLOWLIST:
                errors.append(f"Unknown op: {op.op}")
                continue
            payload = op.payload
            if op.op in {"create_module", "create_module_group"}:
                self._validate_required_fields(op.op, payload, ["module_id", "title"], errors)
                module_id = payload.get("module_id")
                if module_id in module_ids or module_id in new_module_ids:
                    errors.append(f"Duplicate module_id: {module_id}")
                new_module_ids.add(module_id)
            elif op.op == "create_card":
                try:
                    Card.model_validate(payload)
                except ValidationError as exc:
                    errors.append(f"Invalid create_card payload: {exc}")
                card_id = payload.get("card_id")
                if card_id in card_ids or card_id in new_card_ids:
                    errors.append(f"Duplicate card_id: {card_id}")
                new_card_ids.add(card_id)
            elif op.op == "update_card":
                card_id = payload.get("card_id")
                if card_id not in card_ids:
                    errors.append(f"Card missing: {card_id}")
                readonly = CARD_READONLY_FIELDS.intersection(payload)
                if readonly:
                    errors.append(f"update_card modifies readonly fields: {', '.join(sorted(readonly))}")
            elif op.op == "add_submodule":
                self._validate_required_fields(op.op, payload, ["parent_module_id", "module_id", "title"], errors)
                parent_id = payload.get("parent_module_id")
                child_id = payload.get("module_id")
                if parent_id not in module_ids and parent_id not in new_module_ids:
                    errors.append(f"Parent module missing: {parent_id}")
                if parent_id == child_id:
                    errors.append(f"Submodule cycle detected: {parent_id} -> {child_id}")
                module_child_edges.setdefault(parent_id, set()).add(child_id)
                if self._has_cycle(module_child_edges):
                    errors.append(f"Module dependency cycle detected via {parent_id} -> {child_id}")
                    module_child_edges[parent_id].remove(child_id)
            elif op.op == "create_asset":
                try:
                    Asset.model_validate(payload)
                except ValidationError as exc:
                    errors.append(f"Invalid create_asset payload: {exc}")
                asset_id = payload.get("asset_id")
                if asset_id in asset_ids or asset_id in new_asset_ids:
                    errors.append(f"Duplicate asset_id: {asset_id}")
                new_asset_ids.add(asset_id)
                asset_edges[asset_id] = set(payload.get("depends_on", []))
                if self._has_cycle(asset_edges):
                    errors.append(f"Asset dependency cycle detected for new asset: {asset_id}")
            elif op.op == "set_asset_status":
                asset_id = payload.get("asset_id")
                asset = next((item for item in graph.assets if item.asset_id == asset_id), None)
                if not asset:
                    errors.append(f"Asset missing: {asset_id}")
                elif asset.status == "valid" and payload.get("status") == "rejected":
                    errors.append(f"Cannot reject valid asset directly: {asset_id}")
                elif asset.report_selected and payload.get("status") in {"rejected", "archived"}:
                    warnings.append(f"Asset {asset_id} is report_selected; changing status should trigger user confirmation.")
            elif op.op == "connect_dependency":
                asset_id = payload.get("asset_id") or payload.get("from_asset_id")
                depends_on_asset_id = payload.get("depends_on_asset_id") or payload.get("to_asset_id")
                if not asset_id or not depends_on_asset_id:
                    errors.append("connect_dependency requires asset_id/from_asset_id and depends_on_asset_id/to_asset_id")
                    continue
                if asset_id not in asset_ids and asset_id not in new_asset_ids:
                    errors.append(f"Asset missing: {asset_id}")
                if depends_on_asset_id not in asset_ids and depends_on_asset_id not in new_asset_ids:
                    errors.append(f"Dependency asset missing: {depends_on_asset_id}")
                asset_edges.setdefault(asset_id, set()).add(depends_on_asset_id)
                if self._has_cycle(asset_edges):
                    errors.append(f"Asset dependency cycle detected via {asset_id} -> {depends_on_asset_id}")
                    asset_edges[asset_id].remove(depends_on_asset_id)
            elif op.op == "create_claim":
                try:
                    Claim.model_validate(payload)
                except ValidationError as exc:
                    errors.append(f"Invalid create_claim payload: {exc}")
            elif op.op == "create_run":
                try:
                    RunRecord.model_validate(payload)
                except ValidationError as exc:
                    errors.append(f"Invalid create_run payload: {exc}")
            elif op.op == "attach_run_to_card":
                if payload.get("card_id") not in card_ids and payload.get("card_id") not in new_card_ids:
                    errors.append(f"Card missing: {payload.get('card_id')}")
            elif op.op == "attach_asset_to_card":
                if payload.get("asset_id") not in asset_ids and payload.get("asset_id") not in new_asset_ids:
                    errors.append(f"Asset missing: {payload.get('asset_id')}")
                if payload.get("card_id") not in card_ids and payload.get("card_id") not in new_card_ids:
                    errors.append(f"Card missing: {payload.get('card_id')}")
            elif op.op == "add_report_item":
                try:
                    ReportItem.model_validate(payload)
                except ValidationError as exc:
                    errors.append(f"Invalid add_report_item payload: {exc}")
            elif op.op == "semantic_rollback":
                if not patch.requires_user_confirmation:
                    warnings.append("semantic_rollback should require explicit user confirmation.")
                if not payload.get("target_asset_ids") and not payload.get("target_run_ids") and not payload.get("target_card_ids"):
                    warnings.append("semantic_rollback has no explicit targets; Manager explanation should be reviewed carefully.")

        return ValidationResult(valid=not errors, errors=errors, warnings=warnings)

    @staticmethod
    def _validate_required_fields(op_name: str, payload: dict, fields: list[str], errors: list[str]) -> None:
        missing = [field for field in fields if not payload.get(field)]
        if missing:
            errors.append(f"{op_name} missing required fields: {', '.join(missing)}")

    @staticmethod
    def _has_cycle(edges: dict[str, set[str]]) -> bool:
        visited: set[str] = set()
        stack: set[str] = set()

        def visit(node: str) -> bool:
            if node in stack:
                return True
            if node in visited:
                return False
            visited.add(node)
            stack.add(node)
            for child in edges.get(node, set()):
                if visit(child):
                    return True
            stack.remove(node)
            return False

        all_nodes = set(edges)
        for children in edges.values():
            all_nodes.update(children)
        return any(visit(node) for node in all_nodes)
