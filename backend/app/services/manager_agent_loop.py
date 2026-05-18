from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.models.chat import ChatRequest, ChatResponse
from app.services.manager_intent import ManagerIntent
from app.services.manager_planner import DeepSeekManagerPlanner, ManagerPlanDraft, ManagerPlanningError
from app.services.manager_tools import ManagerToolLayer


class ManagerAgentLoop:
    def __init__(
        self,
        planner: DeepSeekManagerPlanner,
        tool_layer: ManagerToolLayer,
        save_proposal: Callable[[str, dict, ManagerPlanDraft], ChatResponse],
        max_steps: int = 8,
    ) -> None:
        self.planner = planner
        self.tool_layer = tool_layer
        self.save_proposal = save_proposal
        self.max_steps = max_steps

    def run(self, project_id: str, snapshot: dict, request: ChatRequest) -> ChatResponse:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "user_request": request.message,
                                "selected_context": request.context.model_dump(),
                                "instruction": "Answer directly or call tools as needed. Do not create proposals unless the user clearly asks for blueprint changes.",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
            }
        ]
        last_proposal_response: ChatResponse | None = None
        for _ in range(self.max_steps):
            response_payload = self.planner.agent_turn(messages, self._tool_specs())
            content = response_payload.get("content", [])
            tool_calls = [block for block in content if block.get("type") == "tool_use"]
            if not tool_calls:
                text = self._extract_text(response_payload).strip()
                if not text:
                    raise ManagerPlanningError("DeepSeek returned neither text nor tool calls.")
                if last_proposal_response:
                    return last_proposal_response.model_copy(update={"message": text})
                return ChatResponse(message=text)

            messages.append({"role": "assistant", "content": content})
            tool_results: list[dict] = []
            for tool_call in tool_calls:
                result = self._execute_tool(project_id, snapshot, request, tool_call)
                if isinstance(result, ChatResponse):
                    last_proposal_response = result
                    payload = {
                        "message": result.message,
                        "proposal": result.proposal.model_dump() if result.proposal else None,
                        "actions": [action.model_dump() for action in result.actions],
                        "warnings": result.warnings,
                    }
                else:
                    payload = result
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call["id"],
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        raise ManagerPlanningError(f"Manager agent exceeded {self.max_steps} tool-loop steps.")

    def _execute_tool(self, project_id: str, snapshot: dict, request: ChatRequest, tool_call: dict) -> dict[str, Any] | ChatResponse:
        name = tool_call.get("name")
        tool_input = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
        if name == "get_project_context":
            return self._context_payload(snapshot)
        if name == "draft_add_module":
            draft = self.tool_layer.try_build_plan(snapshot, request, ManagerIntent(kind="mutation", action="add_module", reason="agent tool call"))
            if not draft:
                raise ManagerPlanningError("draft_add_module could not create a proposal draft for this request.")
            return self.save_proposal(project_id, snapshot, draft)
        if name == "draft_update_existing":
            draft = self.tool_layer.try_build_plan(snapshot, request, ManagerIntent(kind="mutation", action="update_existing", reason="agent tool call"))
            if not draft:
                raise ManagerPlanningError("draft_update_existing could not create a proposal draft for this request.")
            return self.save_proposal(project_id, snapshot, draft)
        if name == "planner_patch":
            planner_request = request
            if isinstance(tool_input.get("instruction"), str) and tool_input["instruction"].strip():
                planner_request = ChatRequest(message=tool_input["instruction"].strip(), context=request.context)
            draft = self.planner.plan(snapshot, planner_request, extra_context={"mode": "agent_tool_loop", "tool_input": tool_input})
            if draft.response_type == "message":
                return {"message": draft.message}
            return self.save_proposal(project_id, snapshot, draft)
        raise ManagerPlanningError(f"Unknown manager tool: {name}")

    @staticmethod
    def _extract_text(response_payload: dict) -> str:
        content = response_payload.get("content", [])
        return "\n".join(
            block["text"]
            for block in content
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        )

    @staticmethod
    def _context_payload(snapshot: dict) -> dict[str, Any]:
        project = snapshot["project"]
        graph = snapshot["graph"]
        return {
            "project": {
                "project_id": project.project_id,
                "name": project.name,
                "current_goal": project.current_goal,
                "status": project.status,
            },
            "cards": [
                {
                    "card_id": card.card_id,
                    "title": card.title,
                    "status": card.status,
                    "summary": card.summary,
                    "linked_modules": card.linked_modules,
                    "linked_assets": card.linked_assets,
                }
                for card in snapshot["cards"]
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
                }
                for asset in graph.assets
            ],
            "open_proposals": [
                proposal.model_dump()
                for proposal in snapshot["proposals"]
                if proposal.status == "proposed"
            ],
        }

    @staticmethod
    def _tool_specs() -> list[dict]:
        return [
            {
                "name": "get_project_context",
                "description": "Read the current project graph, cards, modules, assets, and open proposals. This is read-only.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            {
                "name": "draft_add_module",
                "description": "Draft and save an auditable proposal to add a module/card. Use only when the user clearly asks to add/create a blueprint item.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "instruction": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            {
                "name": "draft_update_existing",
                "description": "Draft and save an auditable proposal to update an existing module/card. Use only for explicit blueprint modification requests.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "instruction": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
            {
                "name": "planner_patch",
                "description": "Draft and save a complex structured proposal through the patch planner. Use for rerun, rollback, cleanup, or changes unsupported by simpler tools.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "instruction": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
        ]
