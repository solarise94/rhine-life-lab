from __future__ import annotations

from collections.abc import Callable

from app.models.chat import ChatRequest, ChatResponse
from app.models.cards import Card
from app.models.patches import Proposal
from app.models.patches import PatchOp
from app.services.manager_planner import ManagerPlanDraft, ManagerPlanningError
from app.services.manager_tools import ManagerToolLayer
from app.services.project_service import ProjectService
from app.services.result_asset_service import ResultAssetService


class ManagerBlueprintTools:
    """Controlled tools exposed to the external manager agent runtime."""

    def __init__(
        self,
        project_service: ProjectService,
        save_proposal,
        replace_proposal: Callable[[str, str, ManagerPlanDraft], Proposal],
        tool_layer: ManagerToolLayer | None = None,
    ) -> None:
        self.project_service = project_service
        self.save_proposal = save_proposal
        self.replace_proposal_callback = replace_proposal
        self.tool_layer = tool_layer or ManagerToolLayer()
        self.result_asset_service = ResultAssetService(project_service)

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
            "proposals": [proposal.model_dump() for proposal in snapshot["proposals"]],
        }

    def plan_blueprint(self, project_id: str, payload: dict) -> dict:
        """Return a read-only multi-layer workflow plan with asset diagnostics.

        This intentionally does not create proposals, patches, modules, cards, or
        assets. It gives the manager agent a safe place to reason about the whole
        workflow before drafting one executable proposal layer.
        """
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph = snapshot["graph"]
        existing_assets = {asset.asset_id: asset for asset in graph.assets}
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ManagerPlanningError("plan_blueprint requires at least one workflow step.")

        planned_assets: dict[str, str] = {}
        normalized_steps: list[dict] = []
        step_ids: set[str] = set()

        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise ManagerPlanningError(f"plan_blueprint step {index} must be an object.")
            step_id = str(raw_step.get("step_id") or f"step_{index}").strip()
            title = str(raw_step.get("title") or "").strip()
            if not title:
                raise ManagerPlanningError(f"plan_blueprint step {step_id} requires title.")
            if step_id in step_ids:
                raise ManagerPlanningError(f"Duplicate plan step_id: {step_id}")
            step_ids.add(step_id)

            input_assets = self._normalize_plan_asset_refs(raw_step.get("input_assets") or raw_step.get("inputs") or [])
            output_assets = self._normalize_plan_asset_refs(raw_step.get("output_assets") or raw_step.get("outputs") or [])
            normalized_step = {
                "step_id": step_id,
                "title": title,
                "specialist": str(raw_step.get("specialist") or raw_step.get("agent") or title).strip(),
                "purpose": str(raw_step.get("purpose") or raw_step.get("summary") or "").strip(),
                "module_id": str(raw_step.get("module_id") or "").strip() or None,
                "card_id": str(raw_step.get("card_id") or "").strip() or None,
                "depends_on_step_ids": [str(item).strip() for item in raw_step.get("depends_on_step_ids") or [] if str(item).strip()],
                "input_assets": input_assets,
                "output_assets": output_assets,
                "notes": str(raw_step.get("notes") or "").strip(),
                "asset_sufficiency": [],
                "is_currently_executable": False,
                "block_reasons": [],
            }
            for asset in output_assets:
                asset_id = asset.get("asset_id")
                if asset_id:
                    planned_assets[asset_id] = step_id
            normalized_steps.append(normalized_step)

        diagnostics: list[dict] = []
        for step in normalized_steps:
            block_reasons: list[str] = []
            asset_sufficiency: list[dict] = []
            for dependency_id in step["depends_on_step_ids"]:
                if dependency_id not in step_ids:
                    block_reasons.append(f"unknown dependency step {dependency_id}")
            for asset_ref in step["input_assets"]:
                asset_id = asset_ref.get("asset_id")
                label = asset_ref.get("label") or asset_id or "unnamed input"
                if not asset_id:
                    state = "unresolved"
                    block_reasons.append(f"input asset is unresolved: {label}")
                    asset_sufficiency.append({"label": label, "asset_id": None, "state": state})
                    continue
                if asset_id in existing_assets and existing_assets[asset_id].status in {"valid", "candidate"}:
                    state = "available_now" if existing_assets[asset_id].status == "valid" else "candidate_input_available"
                    asset_sufficiency.append({"label": label, "asset_id": asset_id, "state": state})
                    continue
                producing_step_id = planned_assets.get(asset_id)
                if producing_step_id:
                    state = "planned_from_workflow"
                    asset_sufficiency.append(
                        {
                            "label": label,
                            "asset_id": asset_id,
                            "state": state,
                            "producer_step_id": producing_step_id,
                        }
                    )
                    if producing_step_id != step["step_id"]:
                        block_reasons.append(f"waiting for planned input {asset_id} from step {producing_step_id}")
                        if producing_step_id not in step["depends_on_step_ids"]:
                            block_reasons.append(f"input {asset_id} should depend on step {producing_step_id}")
                    continue
                state = "missing"
                block_reasons.append(f"missing input asset {asset_id}")
                asset_sufficiency.append({"label": label, "asset_id": asset_id, "state": state})
            step["asset_sufficiency"] = asset_sufficiency
            step["block_reasons"] = block_reasons
            step["is_currently_executable"] = not block_reasons and all(
                item["state"] in {"available_now", "candidate_input_available"} for item in asset_sufficiency
            )
            diagnostics.append(
                {
                    "step_id": step["step_id"],
                    "is_currently_executable": step["is_currently_executable"],
                    "block_reasons": block_reasons,
                    "asset_sufficiency": asset_sufficiency,
                }
            )

        next_executable_step_id = next((step["step_id"] for step in normalized_steps if step["is_currently_executable"]), None)
        return {
            "kind": "blueprint_plan",
            "project_id": project_id,
            "objective": str(payload.get("objective") or payload.get("summary") or "").strip(),
            "assumptions": [str(item).strip() for item in payload.get("assumptions") or [] if str(item).strip()],
            "steps": normalized_steps,
            "diagnostics": diagnostics,
            "next_executable_step_id": next_executable_step_id,
            "write_effect": "none",
            "guidance": (
                "This is a read-only workflow plan. To change the blueprint, draft a proposal only for the "
                "next executable step whose inputs are available now."
            ),
        }

    def review_blueprint_plan(self, project_id: str, payload: dict) -> dict:
        """Deterministically review a blueprint plan without writing anything."""
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph = snapshot["graph"]
        existing_assets = {asset.asset_id: asset for asset in graph.assets}
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
        if not isinstance(plan, dict):
            raise ManagerPlanningError("review_blueprint_plan requires a plan object.")
        raw_steps = plan.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ManagerPlanningError("review_blueprint_plan requires a non-empty steps list.")

        step_records: list[dict] = []
        step_ids: list[str] = []
        errors: list[dict] = []
        warnings: list[dict] = []
        planned_assets: dict[str, str] = {}

        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                errors.append(self._plan_issue("invalid_step", None, f"step {index} must be an object."))
                continue
            step_id = str(raw_step.get("step_id") or f"step_{index}").strip()
            title = str(raw_step.get("title") or "").strip()
            module_id = str(raw_step.get("module_id") or "").strip()
            card_id = str(raw_step.get("card_id") or "").strip()
            depends_on_step_ids = [str(item).strip() for item in raw_step.get("depends_on_step_ids") or [] if str(item).strip()]
            input_assets = self._normalize_plan_asset_refs(raw_step.get("input_assets") or raw_step.get("inputs") or [])
            output_assets = self._normalize_plan_asset_refs(raw_step.get("output_assets") or raw_step.get("outputs") or [])

            if not step_id:
                errors.append(self._plan_issue("missing_step_id", None, f"step {index} is missing step_id."))
                continue
            if step_id in step_ids:
                errors.append(self._plan_issue("duplicate_step_id", step_id, f"duplicate step_id {step_id}."))
                continue
            step_ids.append(step_id)

            if not title:
                errors.append(self._plan_issue("missing_title", step_id, f"step {step_id} requires title."))
            if not module_id:
                errors.append(self._plan_issue("missing_module_id", step_id, f"step {step_id} requires module_id."))
            if not card_id:
                errors.append(self._plan_issue("missing_card_id", step_id, f"step {step_id} requires card_id."))
            if not input_assets:
                errors.append(self._plan_issue("missing_input_assets", step_id, f"step {step_id} requires input_assets."))
            if not output_assets:
                warnings.append(self._plan_issue("missing_output_assets", step_id, f"step {step_id} has no output_assets."))

            for asset in output_assets:
                asset_id = asset.get("asset_id")
                if not asset_id:
                    errors.append(self._plan_issue("missing_output_asset_id", step_id, f"step {step_id} has an output without asset_id."))
                    continue
                planned_assets[asset_id] = step_id

            step_records.append(
                {
                    "step_id": step_id,
                    "title": title,
                    "module_id": module_id or None,
                    "card_id": card_id or None,
                    "depends_on_step_ids": depends_on_step_ids,
                    "input_assets": input_assets,
                    "output_assets": output_assets,
                    "issues": [],
                    "block_reasons": [],
                    "is_currently_executable": False,
                }
            )

        for index, step in enumerate(step_records):
            issues: list[dict] = []
            block_reasons: list[str] = []
            seen_dependencies = set(step["depends_on_step_ids"])
            for dependency_id in step["depends_on_step_ids"]:
                if dependency_id not in step_ids:
                    issues.append(self._plan_issue("unknown_dependency", step["step_id"], f"step {step['step_id']} depends on unknown step {dependency_id}."))
            for asset_ref in step["input_assets"]:
                label = asset_ref.get("label") or asset_ref.get("asset_id") or "unnamed input"
                asset_id = asset_ref.get("asset_id")
                if not asset_id:
                    issues.append(self._plan_issue("missing_input_asset_id", step["step_id"], f"step {step['step_id']} input {label} is missing asset_id."))
                    continue
                if asset_id in existing_assets and existing_assets[asset_id].status in {"valid", "candidate"}:
                    continue
                producing_step_id = planned_assets.get(asset_id)
                if producing_step_id:
                    if producing_step_id != step["step_id"]:
                        block_reasons.append(f"waiting for planned input {asset_id} from step {producing_step_id}")
                    if producing_step_id not in seen_dependencies:
                        issues.append(
                            self._plan_issue(
                                "missing_dependency",
                                step["step_id"],
                                f"step {step['step_id']} uses planned asset {asset_id} from {producing_step_id} but does not depend on it.",
                            )
                        )
                    producer_index = step_ids.index(producing_step_id)
                    if producer_index >= index:
                        issues.append(
                            self._plan_issue(
                                "downstream_order",
                                step["step_id"],
                                f"step {step['step_id']} uses {asset_id} before its producer step {producing_step_id} is executed.",
                            )
                        )
                    continue
                issues.append(
                    self._plan_issue(
                        "missing_input_asset",
                        step["step_id"],
                        f"step {step['step_id']} requires unavailable asset {asset_id} ({label}).",
                    )
                )

            step["issues"] = issues
            step["block_reasons"] = block_reasons
            step["is_currently_executable"] = not issues and not block_reasons
            if issues:
                errors.extend(issues)

        approved = not errors
        next_executable_step_id = next((step["step_id"] for step in step_records if step["is_currently_executable"]), None)
        return {
            "kind": "blueprint_plan_review",
            "project_id": project_id,
            "approved": approved,
            "errors": errors,
            "warnings": warnings,
            "next_executable_step_id": next_executable_step_id,
            "step_reviews": step_records,
        }

    def save_patch_proposal(self, project_id: str, payload: dict) -> ChatResponse:
        snapshot = self.project_service.get_project_snapshot(project_id)
        draft = ManagerPlanDraft.model_validate(
            {
                "response_type": "proposal",
                "message": payload.get("message") or payload.get("summary") or "已生成蓝图变更 proposal。",
                "title": payload.get("title"),
                "summary": payload.get("summary"),
                "impact_summary": payload.get("impact_summary"),
                "patch_type": payload.get("patch_type"),
                "reason": payload.get("reason"),
                "ops": payload.get("ops") or [],
            }
        )
        draft.ensure_valid()
        return self.save_proposal(project_id, snapshot, draft)

    def delete_module_proposal(self, project_id: str, payload: dict) -> ChatResponse:
        module_id = str(payload.get("module_id") or "").strip()
        reason = str(payload.get("reason") or payload.get("message") or "").strip()
        if not module_id:
            raise ManagerPlanningError("delete_module requires module_id.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph = snapshot["graph"]
        module = next((item for item in graph.modules if item.module_id == module_id), None)
        if not module:
            raise ManagerPlanningError(f"Module not found: {module_id}")
        linked_cards = [card for card in snapshot["cards"] if module_id in card.linked_modules]
        ops: list[PatchOp] = [
            PatchOp(
                op="update_module",
                payload={
                    "module_id": module.module_id,
                    "status": "cancelled",
                    "summary": f"{module.summary} 已按用户要求从蓝图中取消。",
                },
            )
        ]
        for parent in graph.modules:
            if any(item.module_id == module_id for item in parent.submodules):
                ops.append(
                    PatchOp(
                        op="remove_submodule",
                        payload={
                            "parent_module_id": parent.module_id,
                            "module_id": module_id,
                        },
                    )
                )
        for card in linked_cards:
            ops.append(
                PatchOp(
                    op="update_card",
                    payload={
                        "card_id": card.card_id,
                        "status": "cancelled",
                        "manager_review": f"按用户要求取消该模块。{reason}",
                        "next_actions": ["恢复模块", "查看影响"],
                    },
                )
            )
        title = module.title
        draft = ManagerPlanDraft(
            response_type="proposal",
            message=f"我会生成 proposal 取消「{title}」模块。",
            title=f"删除 {title}",
            summary=f"将「{title}」模块及关联 card 标记为 cancelled。",
            impact_summary="不会删除历史结果文件；接受后会从模块组层级中移除该模块，并保留审计记录。",
            patch_type="delete_module",
            reason=reason or f"用户要求删除模块 {module_id}",
            ops=ops,
        )
        return self.save_proposal(project_id, snapshot, draft)

    def delete_card_proposal(self, project_id: str, payload: dict) -> ChatResponse:
        card_id = str(payload.get("card_id") or "").strip()
        module_id = str(payload.get("module_id") or "").strip()
        reason = str(payload.get("reason") or payload.get("message") or "").strip()
        if not card_id and not module_id:
            raise ManagerPlanningError("delete_card requires card_id or module_id.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        card = self._find_card(snapshot, card_id=card_id, module_id=module_id)
        if not card:
            raise ManagerPlanningError(f"Card not found: {card_id or module_id}")

        ops: list[PatchOp] = [
            PatchOp(
                op="update_card",
                payload=self._card_update_payload(
                    card,
                    status="cancelled",
                    manager_review=f"按用户要求取消该卡片。{reason}".strip(),
                    next_actions=["恢复卡片", "查看影响"],
                ),
            )
        ]
        title = card.title
        draft = ManagerPlanDraft(
            response_type="proposal",
            message=f"我会生成 proposal 取消「{title}」卡片。",
            title=f"删除 {title}",
            summary=f"将「{title}」卡片标记为 cancelled。",
            impact_summary="只会取消该 card 的可见状态，不会删除历史审计记录或已生成结果。",
            patch_type="update_card",
            reason=reason or f"用户要求删除卡片 {card.card_id}",
            ops=ops,
        )
        return self.save_proposal(project_id, snapshot, draft)

    def restore_module_proposal(self, project_id: str, payload: dict) -> ChatResponse:
        module_id = str(payload.get("module_id") or "").strip()
        reason = str(payload.get("reason") or payload.get("message") or "").strip()
        if not module_id:
            raise ManagerPlanningError("restore_module requires module_id.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        graph = snapshot["graph"]
        module = next((item for item in graph.modules if item.module_id == module_id), None)
        if not module:
            raise ManagerPlanningError(f"Module not found: {module_id}")
        if module.status != "cancelled":
            raise ManagerPlanningError(f"Module {module_id} is {module.status}, not cancelled.")
        linked_cards = [card for card in snapshot["cards"] if module_id in card.linked_modules]
        next_status = str(payload.get("status") or "planned").strip()
        if next_status not in {"proposed", "planned"}:
            raise ManagerPlanningError("restore_module status must be proposed or planned.")

        ops: list[PatchOp] = [
            PatchOp(
                op="update_module",
                payload={
                    "module_id": module.module_id,
                    "status": next_status,
                    "summary": self._strip_cancelled_suffix(module.summary),
                },
            )
        ]
        for card in linked_cards:
            ops.append(
                PatchOp(
                    op="update_card",
                    payload={
                        "card_id": card.card_id,
                        "status": next_status,
                        "manager_review": f"按用户要求恢复该模块。{reason}",
                        "next_actions": ["开始执行", "修改方案", "取消模块"],
                    },
                )
            )
        title = module.title
        draft = ManagerPlanDraft(
            response_type="proposal",
            message=f"我会生成 proposal 恢复「{title}」模块。",
            title=f"恢复 {title}",
            summary=f"将「{title}」模块及关联 card 恢复为 {next_status}。",
            impact_summary="不会创建重复模块；只恢复现有 module/card 的状态和可执行动作。",
            patch_type="update_card",
            reason=reason or f"用户要求恢复模块 {module_id}",
            ops=ops,
        )
        return self.save_proposal(project_id, snapshot, draft)

    def restore_card_proposal(self, project_id: str, payload: dict) -> ChatResponse:
        card_id = str(payload.get("card_id") or "").strip()
        module_id = str(payload.get("module_id") or "").strip()
        reason = str(payload.get("reason") or payload.get("message") or "").strip()
        next_status = str(payload.get("status") or "planned").strip()
        if next_status not in {"proposed", "planned"}:
            raise ManagerPlanningError("restore_card status must be proposed or planned.")
        if not card_id and not module_id:
            raise ManagerPlanningError("restore_card requires card_id or module_id.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        card = self._find_card(snapshot, card_id=card_id, module_id=module_id)
        if not card:
            raise ManagerPlanningError(f"Card not found: {card_id or module_id}")
        if card.status != "cancelled":
            raise ManagerPlanningError(f"Card {card.card_id} is {card.status}, not cancelled.")

        ops: list[PatchOp] = [
            PatchOp(
                op="update_card",
                payload=self._card_update_payload(
                    card,
                    status=next_status,
                    manager_review=f"按用户要求恢复该卡片。{reason}".strip(),
                    next_actions=["开始执行", "修改方案", "取消模块"],
                ),
            )
        ]
        title = card.title
        draft = ManagerPlanDraft(
            response_type="proposal",
            message=f"我会生成 proposal 恢复「{title}」卡片。",
            title=f"恢复 {title}",
            summary=f"将「{title}」卡片恢复为 {next_status}。",
            impact_summary="不会创建重复卡片；只恢复现有 card 的状态和可执行动作。",
            patch_type="update_card",
            reason=reason or f"用户要求恢复卡片 {card.card_id}",
            ops=ops,
        )
        return self.save_proposal(project_id, snapshot, draft)

    def modify_proposal(self, project_id: str, proposal_id: str, payload: dict) -> dict:
        draft = ManagerPlanDraft.model_validate(
            {
                "response_type": "proposal",
                "message": payload.get("message") or payload.get("summary") or "已更新 proposal。",
                "title": payload.get("title"),
                "summary": payload.get("summary"),
                "impact_summary": payload.get("impact_summary"),
                "patch_type": payload.get("patch_type"),
                "reason": payload.get("reason"),
                "ops": payload.get("ops") or [],
            }
        )
        draft.ensure_valid()
        proposal = self.replace_proposal_callback(project_id, proposal_id, draft)
        patch_payload = self.project_service.graph_store(project_id).load_patch(proposal.patch_id)
        if not patch_payload:
            raise ManagerPlanningError(f"Patch not found for proposal {proposal.proposal_id}")
        return {"proposal": proposal.model_dump(), "patch": patch_payload}

    def read_result_asset(self, project_id: str, asset_id: str) -> dict:
        if not asset_id:
            raise ManagerPlanningError("read_result_asset requires asset_id.")
        return self.result_asset_service.get_asset_detail(project_id, asset_id)

    @staticmethod
    def _find_card(snapshot: dict, card_id: str = "", module_id: str = "") -> Card | None:
        if card_id:
            return next((card for card in snapshot["cards"] if card.card_id == card_id), None)
        if module_id:
            return next((card for card in snapshot["cards"] if module_id in card.linked_modules), None)
        return None

    @staticmethod
    def _card_update_payload(card: Card, **overrides) -> dict:
        payload = {
            "card_id": card.card_id,
            "title": card.title,
            "status": card.status,
            "summary": card.summary,
            "why": card.why,
            "inputs": [item.model_dump() for item in card.inputs],
            "outputs": [item.model_dump() for item in card.outputs],
            "key_findings": list(card.key_findings),
            "manager_review": card.manager_review,
            "next_actions": list(card.next_actions),
            "linked_modules": list(card.linked_modules),
            "linked_assets": list(card.linked_assets),
            "progress_note": card.progress_note,
        }
        payload.update({key: value for key, value in overrides.items() if value is not None})
        return payload

    @staticmethod
    def _strip_cancelled_suffix(summary: str) -> str:
        return summary.replace(" 已按用户要求从蓝图中取消。", "").strip()

    @staticmethod
    def _normalize_plan_asset_refs(raw_assets) -> list[dict]:
        refs: list[dict] = []
        if not isinstance(raw_assets, list):
            return refs
        for index, raw_asset in enumerate(raw_assets, start=1):
            if isinstance(raw_asset, str):
                value = raw_asset.strip()
                if value:
                    refs.append({"label": value, "asset_id": value})
                continue
            if not isinstance(raw_asset, dict):
                continue
            label = str(raw_asset.get("label") or raw_asset.get("title") or raw_asset.get("asset_id") or f"asset_{index}").strip()
            asset_id = str(raw_asset.get("asset_id") or raw_asset.get("id") or "").strip()
            status = str(raw_asset.get("status") or "").strip()
            refs.append(
                {
                    "label": label,
                    "asset_id": asset_id or None,
                    **({"status": status} if status else {}),
                }
            )
        return refs

    @staticmethod
    def _plan_issue(code: str, step_id: str | None, message: str) -> dict:
        return {
            "code": code,
            "step_id": step_id,
            "message": message,
        }
