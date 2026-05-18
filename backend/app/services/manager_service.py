from __future__ import annotations

import re
import json
from urllib import error, request as url_request

from app.models.chat import ChatAction, ChatRequest, ChatResponse
from app.models.patches import GraphPatch, Proposal
from app.core.config import get_settings
from app.services.manager_blueprint_tools import ManagerBlueprintTools
from app.services.manager_patch_compiler import ManagerPatchCompiler
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanDraft
from app.services.manager_planner import ManagerPlanningError
from app.services.manager_tools import ManagerToolLayer
from app.services.manager_workflow import ManagerWorkflow
from app.services.patch_validator import PatchValidator
from app.services.project_service import ProjectService
from app.services.utils import utc_now


class ManagerService:
    def __init__(
        self,
        project_service: ProjectService,
        planner: DeepSeekManagerPlanner | None = None,
        tool_layer: ManagerToolLayer | None = None,
    ) -> None:
        self.project_service = project_service
        self.planner = planner or DeepSeekManagerPlanner()
        self.tool_layer = tool_layer or ManagerToolLayer()
        self.settings = get_settings()
        self.blueprint_tools = ManagerBlueprintTools(
            project_service=self.project_service,
            save_proposal=self._save_proposal_draft,
            replace_proposal=self.replace_proposal_with_draft,
            tool_layer=self.tool_layer,
        )
        self.workflow = ManagerWorkflow(
            project_service=self.project_service,
            planner=self.planner,
            tool_layer=self.tool_layer,
            save_proposal=self._save_proposal_draft,
        )

    def chat(self, project_id: str, request: ChatRequest) -> ChatResponse:
        if self.settings.manager_backend == "pi" and self.planner.__class__ is DeepSeekManagerPlanner:
            return self._chat_via_pi(project_id, request)
        return self.workflow.invoke(project_id, request)

    def _chat_via_pi(self, project_id: str, chat_request: ChatRequest) -> ChatResponse:
        token = self.settings.internal_tool_token.get_secret_value() if self.settings.internal_tool_token else ""
        if not token:
            raise ManagerPlanningError("BLUEPRINT_INTERNAL_TOOL_TOKEN is not configured.")
        payload = {
            "project_id": project_id,
            "message": chat_request.message,
            "context": chat_request.context.model_dump(),
            "backend_api_base_url": self.settings.backend_api_base_url.rstrip("/"),
            "internal_tool_token": token,
        }
        endpoint = f"{self.settings.pi_manager_url.rstrip('/')}/chat"
        http_request = url_request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with url_request.urlopen(http_request, timeout=self.settings.manager_timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ManagerPlanningError(f"Pi manager failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise ManagerPlanningError(f"Pi manager request failed: {exc.reason}") from exc
        except (TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ManagerPlanningError(f"Pi manager request failed: {exc}") from exc
        return ChatResponse.model_validate(response_payload)

    def _save_proposal_draft(self, project_id: str, snapshot: dict, draft: ManagerPlanDraft) -> ChatResponse:
        proposal, patch = self._materialize_proposal(draft)
        patch, warnings = self._normalize_and_validate_patch(project_id, snapshot, patch)
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposals = [item for item in proposals if item.proposal_id != proposal.proposal_id]
        proposal.consistency_warnings = warnings
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
            warnings=warnings,
        )

    def get_proposal(self, project_id: str, proposal_id: str) -> Proposal:
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        return next(item for item in proposals if item.proposal_id == proposal_id)

    def mark_proposal_status(self, project_id: str, proposal_id: str, status: str) -> Proposal:
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposal = next(item for item in proposals if item.proposal_id == proposal_id)
        proposal.status = status
        proposal.updated_at = utc_now()
        store.save_proposals(proposals)
        return proposal

    def accept_proposal(self, project_id: str, proposal_id: str) -> Proposal:
        return self.mark_proposal_status(project_id, proposal_id, "accepted")

    def reject_proposal(self, project_id: str, proposal_id: str) -> Proposal:
        return self.mark_proposal_status(project_id, proposal_id, "rejected")

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
        patch, warnings = self._normalize_and_validate_patch(project_id, snapshot, patch)
        updated_proposal.created_at = proposal.created_at
        updated_proposal.consistency_warnings = self._proposal_consistency_warnings(updated_proposal, patch, proposal, existing_patch)
        updated_proposal.consistency_warnings.extend(warnings)
        proposals = [item for item in proposals if item.proposal_id != proposal_id]
        proposals.append(updated_proposal)
        store.save_proposals(proposals)
        store.save_patch(patch.patch_id, patch.model_dump())
        return updated_proposal

    def replace_proposal_with_draft(self, project_id: str, proposal_id: str, draft: ManagerPlanDraft) -> Proposal:
        store = self.project_service.graph_store(project_id)
        proposals = store.load_proposals()
        proposal = next(item for item in proposals if item.proposal_id == proposal_id)
        existing_patch = store.load_patch(proposal.patch_id)
        if not existing_patch:
            raise ValueError(f"Patch not found for proposal {proposal_id}")
        if draft.response_type != "proposal":
            raise ValueError("Structured proposal replacement must be a proposal response.")
        snapshot = self.project_service.get_project_snapshot(project_id)
        updated_proposal, patch = self._materialize_proposal(draft, proposal_id=proposal_id)
        patch, warnings = self._normalize_and_validate_patch(project_id, snapshot, patch)
        updated_proposal.created_at = proposal.created_at
        updated_proposal.consistency_warnings = self._proposal_consistency_warnings(updated_proposal, patch, proposal, existing_patch)
        updated_proposal.consistency_warnings.extend(warnings)
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

    def _normalize_and_validate_patch(self, project_id: str, snapshot: dict, patch: GraphPatch) -> tuple[GraphPatch, list[str]]:
        compiler = ManagerPatchCompiler.from_snapshot(snapshot)
        normalized_patch = compiler.normalize_patch(patch)
        validator = PatchValidator(self.project_service)
        validation = validator.validate_patch(project_id, normalized_patch)
        if not validation.valid:
            raise ManagerPlanningError("Manager proposal validation failed: " + "; ".join(validation.errors))
        return normalized_patch, validation.warnings
