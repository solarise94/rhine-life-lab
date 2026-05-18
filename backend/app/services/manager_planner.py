from __future__ import annotations

import json
from typing import Literal
from urllib import error, request

from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings, get_settings
from app.models.chat import ChatRequest
from app.models.patches import PatchOp, PatchType


SUPPORTED_OPS = [
    "create_module",
    "create_module_group",
    "add_submodule",
    "create_card",
    "update_card",
    "set_card_status",
    "set_module_status",
    "create_asset",
    "set_asset_status",
    "create_claim",
    "attach_asset_to_card",
    "create_run",
    "attach_run_to_card",
    "add_report_item",
    "mark_downstream_stale",
    "semantic_rollback",
]

SYSTEM_PROMPT = """You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

Your job is to read the user's intent plus the current project graph, then return exactly one structured manager decision through the provided tool.

Rules:
- Use only the tool `submit_manager_plan`.
- `response_type="proposal"` when the user asks to add, change, rerun, review, rollback, or otherwise mutate project state.
- `response_type="message"` only when no graph mutation should happen yet.
- When proposing graph mutations, use only supported ops from the provided context.
- Reuse existing ids from context when referring to existing cards/modules/assets/runs.
- For new ids, use stable snake_case prefixes like `module_`, `module_group_`, `card_`, `asset_`, `claim_`, `run_`.
- Keep summaries concrete and user-facing.
- Never output free text outside the tool call.
"""


class ManagerPlanningError(RuntimeError):
    pass


class ManagerPlanDraft(BaseModel):
    response_type: Literal["proposal", "message"]
    message: str
    title: str | None = None
    summary: str | None = None
    impact_summary: str | None = None
    patch_type: PatchType | None = None
    reason: str | None = None
    ops: list[PatchOp] = Field(default_factory=list)

    def ensure_valid(self) -> None:
        if self.response_type == "proposal":
            missing = [
                name
                for name, value in {
                    "title": self.title,
                    "summary": self.summary,
                    "impact_summary": self.impact_summary,
                    "patch_type": self.patch_type,
                    "reason": self.reason,
                }.items()
                if not value
            ]
            if missing:
                raise ManagerPlanningError(f"Manager model omitted required proposal fields: {', '.join(missing)}")
            if not self.ops:
                raise ManagerPlanningError("Manager model returned a proposal without patch ops.")


class DeepSeekManagerPlanner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        api_key = self.settings.deepseek_api_key.get_secret_value() if self.settings.deepseek_api_key else ""
        if not api_key:
            raise ManagerPlanningError("BLUEPRINT_DEEPSEEK_API_KEY is not configured.")

        payload = {
            "model": self.settings.manager_model,
            "max_tokens": self.settings.manager_max_tokens,
            "temperature": self.settings.manager_temperature,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(self._build_context(snapshot, chat_request, extra_context), ensure_ascii=False, indent=2),
                        }
                    ],
                }
            ],
            "tools": [
                {
                    "name": "submit_manager_plan",
                    "description": "Return the manager decision as structured data.",
                    "input_schema": ManagerPlanDraft.model_json_schema(),
                }
            ],
            "tool_choice": {"type": "tool", "name": "submit_manager_plan"},
        }
        endpoint = f"{self.settings.deepseek_api_base_url.rstrip('/')}/v1/messages"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )

        try:
            with request.urlopen(http_request, timeout=self.settings.manager_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ManagerPlanningError(f"DeepSeek request failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise ManagerPlanningError(f"DeepSeek request failed: {exc.reason}") from exc

        try:
            response_payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManagerPlanningError("DeepSeek returned invalid JSON at the HTTP layer.") from exc

        tool_input = self._extract_tool_input(response_payload)
        try:
            draft = ManagerPlanDraft.model_validate(tool_input)
        except ValidationError as exc:
            raise ManagerPlanningError(f"DeepSeek returned an invalid manager plan: {exc}") from exc
        draft.ensure_valid()
        return draft

    @staticmethod
    def _extract_tool_input(response_payload: dict) -> dict:
        content = response_payload.get("content", [])
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "submit_manager_plan":
                tool_input = block.get("input")
                if isinstance(tool_input, dict):
                    return tool_input
        raise ManagerPlanningError("DeepSeek did not return the required submit_manager_plan tool call.")

    def _build_context(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> dict:
        project = snapshot["project"]
        cards = snapshot["cards"]
        graph = snapshot["graph"]
        proposals = snapshot["proposals"]
        context = {
            "user_request": chat_request.message,
            "selected_context": chat_request.context.model_dump(),
            "project": {
                "project_id": project.project_id,
                "name": project.name,
                "current_goal": project.current_goal,
                "status": project.status,
            },
            "supported_patch_types": ["add_module", "add_module_group", "update_card", "review_run", "semantic_rollback"],
            "supported_ops": SUPPORTED_OPS,
            "cards": [
                {
                    "card_id": card.card_id,
                    "card_type": card.card_type,
                    "title": card.title,
                    "status": card.status,
                    "summary": card.summary,
                    "linked_modules": card.linked_modules,
                    "linked_runs": card.linked_runs,
                    "linked_assets": card.linked_assets,
                    "next_actions": card.next_actions,
                }
                for card in cards
            ],
            "modules": [
                {
                    "module_id": module.module_id,
                    "title": module.title,
                    "type": module.type,
                    "status": module.status,
                    "summary": module.summary,
                    "depends_on_assets": module.depends_on_assets,
                    "expected_outputs": module.expected_outputs,
                    "submodules": [item.model_dump() for item in module.submodules],
                }
                for module in graph.modules
            ],
            "assets": [
                {
                    "asset_id": asset.asset_id,
                    "asset_type": asset.asset_type,
                    "title": asset.title,
                    "status": asset.status,
                    "summary": asset.summary,
                    "path": asset.path,
                    "depends_on": asset.depends_on,
                    "report_selected": asset.report_selected,
                }
                for asset in graph.assets
            ],
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "text": claim.text,
                    "status": claim.status,
                    "depends_on_assets": claim.depends_on_assets,
                    "report_selected": claim.report_selected,
                }
                for claim in graph.claims
            ],
            "open_proposals": [
                {
                    "proposal_id": proposal.proposal_id,
                    "patch_id": proposal.patch_id,
                    "title": proposal.title,
                    "summary": proposal.summary,
                    "status": proposal.status,
                }
                for proposal in proposals
                if proposal.status == "proposed"
            ],
        }
        if extra_context:
            context["extra_context"] = extra_context
        return context
