from __future__ import annotations

from collections.abc import Callable

from app.models.chat import ChatRequest, ChatResponse
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
    def _strip_cancelled_suffix(summary: str) -> str:
        return summary.replace(" 已按用户要求从蓝图中取消。", "").strip()
