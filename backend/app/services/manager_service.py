from __future__ import annotations

import re

from app.models.chat import ChatAction, ChatRequest, ChatResponse
from app.models.patches import GraphPatch, Proposal
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanDraft
from app.services.project_service import ProjectService
from app.services.utils import utc_now


class ManagerService:
    def __init__(self, project_service: ProjectService, planner: DeepSeekManagerPlanner | None = None) -> None:
        self.project_service = project_service
        self.planner = planner or DeepSeekManagerPlanner()

    def chat(self, project_id: str, request: ChatRequest) -> ChatResponse:
        snapshot = self.project_service.get_project_snapshot(project_id)
        draft = self.planner.plan(snapshot, request)
        if draft.response_type == "message":
            return ChatResponse(message=draft.message)

        proposal, patch = self._materialize_proposal(draft)
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposals = [item for item in proposals if item.proposal_id != proposal.proposal_id]
        proposals.append(proposal)
        store.save_proposals(proposals)
        store.save_patch(patch.patch_id, patch.model_dump())
        return ChatResponse(
            message=proposal.summary,
            proposal=proposal,
            actions=[
                ChatAction(label="接受提案", action="accept_proposal"),
                ChatAction(label="修改提案", action="modify_proposal"),
                ChatAction(label="查看影响", action="view_impact"),
            ],
        )

    def accept_proposal(self, project_id: str, proposal_id: str) -> Proposal:
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposal = next(item for item in proposals if item.proposal_id == proposal_id)
        proposal.status = "accepted"
        proposal.updated_at = utc_now()
        store.save_proposals(proposals)
        return proposal

    def reject_proposal(self, project_id: str, proposal_id: str) -> Proposal:
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposal = next(item for item in proposals if item.proposal_id == proposal_id)
        proposal.status = "rejected"
        proposal.updated_at = utc_now()
        store.save_proposals(proposals)
        return proposal

    def modify_proposal(self, project_id: str, proposal_id: str, request: ChatRequest) -> Proposal:
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposal = next(item for item in proposals if item.proposal_id == proposal_id)
        existing_patch = store.load_patch(proposal.patch_id)
        if not existing_patch:
            raise ValueError(f"Patch not found for proposal {proposal_id}")
        snapshot = self.project_service.get_project_snapshot(project_id)
        draft = self.planner.plan(
            snapshot,
            request,
            extra_context={
                "mode": "modify_proposal",
                "existing_proposal": proposal.model_dump(),
                "existing_patch": existing_patch,
            },
        )
        if draft.response_type != "proposal":
            raise ValueError("Manager modify_proposal must return a proposal response.")
        updated_proposal, patch = self._materialize_proposal(draft, proposal_id=proposal_id)
        updated_proposal.created_at = proposal.created_at
        updated_proposal.consistency_warnings = self._proposal_consistency_warnings(updated_proposal, patch, proposal, existing_patch)
        proposals = [item for item in proposals if item.proposal_id != proposal_id]
        proposals.append(updated_proposal)
        store.save_proposals(proposals)
        store.save_patch(patch.patch_id, patch.model_dump())
        return updated_proposal

    @staticmethod
    def _materialize_proposal(draft: ManagerPlanDraft, proposal_id: str | None = None) -> tuple[Proposal, GraphPatch]:
        now = utc_now()
        timestamp = re.sub(r"[^0-9]", "", now)
        slug_source = draft.title or draft.patch_type or "manager_plan"
        slug = re.sub(r"[^a-z0-9]+", "_", slug_source.lower()).strip("_") or "manager_plan"
        is_modification = proposal_id is not None
        proposal_id = proposal_id or f"proposal_{slug}_{timestamp}"
        patch_id = proposal_id.replace("proposal_", "patch_", 1)
        if is_modification:
            patch_id = f"{patch_id}_{timestamp}"
        proposal = Proposal(
            proposal_id=proposal_id,
            patch_id=patch_id,
            title=draft.title or "Manager Proposal",
            summary=draft.summary or draft.message,
            impact_summary=draft.impact_summary or draft.message,
            status="proposed",
            created_at=now,
            updated_at=now,
        )
        patch = GraphPatch(
            patch_id=patch_id,
            patch_type=draft.patch_type,
            source="manager_ai",
            reason=draft.reason or draft.message,
            ops=draft.ops,
        )
        return proposal, patch

    @staticmethod
    def _proposal_consistency_warnings(new_proposal: Proposal, new_patch: GraphPatch, previous_proposal: Proposal, previous_patch: dict) -> list[str]:
        warnings: list[str] = []
        previous_patch_type = previous_patch.get("patch_type")
        if previous_patch_type and previous_patch_type != new_patch.patch_type:
            warnings.append(f"Patch type changed from {previous_patch_type} to {new_patch.patch_type}.")
        if previous_proposal.title != new_proposal.title:
            warnings.append(f"Proposal title changed from “{previous_proposal.title}” to “{new_proposal.title}”.")
        if not new_patch.ops:
            warnings.append("Modified proposal does not contain executable patch ops.")
        return warnings
