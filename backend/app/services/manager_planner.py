from __future__ import annotations

import json
from http import client
from typing import Any, Literal
from urllib import error, request

from pydantic import BaseModel, Field, ValidationError

from app.core.config import Settings, get_settings
from app.models.chat import ChatRequest
from app.models.patches import PatchOp, PatchType


SUPPORTED_OPS = [
    "create_module",
    "create_module_group",
    "add_submodule",
    "remove_submodule",
    "create_card",
    "update_card",
    "update_module",
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

LEGACY_TOOL_MODEL_ALIASES = {
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}

SYSTEM_PROMPT = """You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

Your job is to read the user's intent plus the current project graph, then return exactly one structured manager decision through the provided tool.

Rules:
- Use only the tool `submit_manager_plan`.
- `response_type="proposal"` when the user asks to add, change, rerun, review, rollback, or otherwise mutate project state. The result should be a direct card/edit plan, not a blueprint design workflow.
- `response_type="message"` only when no graph mutation should happen yet.
- Never output free text outside the tool call.
- Keep summaries concrete and user-facing.

You must follow the patch contract strictly:
- Use only `supported_patch_types` and `supported_ops` from the provided context.
- Reuse existing ids from context when referring to existing cards/modules/assets/runs.
- For new ids, use stable snake_case prefixes like `module_`, `module_group_`, `card_`, `asset_`, `claim_`, `run_`.
- Do not invent fields that are not listed in the op contract.
- Do not use `update_card` to modify a module. Cards and modules are different objects.
- Do not use module ids where a card id is required, or card ids where a module id is required.
- The DAG is already visible in the UI. Do not narrate the full graph back to the user unless they explicitly ask for a graph recap.
- Respect `selected_context.script_preference` when creating analysis cards. Treat it as a soft user preference, not a hard constraint.
- Respect `selected_context.python_runtime` and `selected_context.r_runtime` as preferred execution runtimes when planning or updating analysis cards.
- If `script_preference` is `auto` and a new bioinformatics card could reasonably be implemented in either Python or R, ask the user for a preference before creating cards when that choice materially affects the workflow.
- When a concrete preference is known, add it to each new or updated analysis card through `executor_context.instruction_blocks`, e.g. "Soft script preference: prefer R scripts when practical; use Python if it is more reliable for this task."

Before you return the tool call, do an internal self-check:
1. Verify every op uses the correct target object type.
2. Verify every op includes all required fields.
3. Verify referenced existing ids actually appear in the provided context.
4. If an op would fail validation, rewrite it before returning.
5. If the user intent cannot yet be expressed as a valid patch, return `response_type="message"` and explain what is missing.

Use these op contracts:
- `create_module`: requires `module_id`, `title`. Optional: `status`, `summary`, `depends_on_assets`, `expected_outputs`, `linked_cards`.
- `create_module_group`: requires `module_id`, `title`. Optional: `status`, `summary`, `depends_on_assets`, `expected_outputs`, `linked_cards`.
- `add_submodule`: requires `parent_module_id`, `module_id`, `title`. `parent_module_id` must be an existing module group. `module_id` should usually refer to a module created in the same change or an existing module.
- `create_card`: must create a valid card object. Required fields include `card_id`, `card_type`, `title`, `status`, `summary`. Prefer also setting `step`, `why`, `inputs`, `outputs`, `key_findings`, `manager_review`, `next_actions`, `linked_modules`, `linked_runs`, `linked_assets`, `executor_context`.
- `update_card`: requires `card_id` of an existing card. Only use card fields such as `step`, `title`, `status`, `summary`, `why`, `inputs`, `outputs`, `key_findings`, `manager_review`, `next_actions`, `linked_modules`, `linked_assets`, `progress_note`, `executor_context`. Never use it for modules. To delete a card, set `status` to `cancelled` and keep the card metadata intact for auditability.
- Every `outputs[]` entry must be an explicit output contract with `role`, `label`, and `artifact_class`. `accepted_formats` is optional and may contain multiple acceptable formats. Do not create label-only outputs.
- `update_module`: requires `module_id` of an existing module. Only use module fields such as `title`, `status`, `summary`, `depends_on_assets`, `expected_outputs`, `linked_cards`.
- `set_card_status`: requires `card_id`, `status`.
- `set_module_status`: requires `module_id`, `status`.
- `create_run`: requires a full run record including `run_id`, `card_id`, `status`, `title`, `summary`, `started_at`. Only use when you really intend to create a run record directly.
- `mark_downstream_stale`: use `asset_ids` as the list of changed upstream assets.

Preferred patterns:
- To add a new analysis card: usually create a module first, then create a card linked to that module.
- To add a submodule under a module group: create the module, then add it via `add_submodule`, then create its card.
- To modify the wording of an existing card/module pair: use `update_card` for the card and `update_module` for the module.
- For multi-step workflows, think through the full dependency chain first, but return only the next executable layer in a single proposal. Do not include downstream cards that depend on assets planned in the same proposal.
"""

CHAT_SYSTEM_PROMPT = """You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

You are in normal chat mode. Answer the user's question conversationally using the provided project context.

Rules:
- Do not create, modify, or imply that you applied blueprint changes.
- If the user asks to mutate project state, tell them you can prepare an auditable proposal and they should state the desired change clearly.
- The DAG is already visible in the UI; do not narrate the full graph unless the user explicitly asks for a recap.
- Keep answers concise and concrete.
"""

HARNESS_SYSTEM_PROMPT = """You are the Manager AI for Blueprint RE, a bioinformatics workflow manager with tools.

You can either answer directly or call tools. You do not directly modify files or apply patches.

Tool rules:
- Use `get_project_context` when you need current graph/cards/assets/proposals.
- For ordinary greetings, explanations, reviews, suggestions, and "what else can we do" questions, answer directly after inspecting context.
- The DAG is already visible in the UI; do not narrate the full graph unless the user explicitly asks for a recap.
- Use direct card tools when the user clearly asks to add, create, modify, delete, rerun, rollback, or otherwise change the blueprint.
- Direct card tools apply the requested card change immediately after validation.
- Never claim a blueprint change has been applied unless the tool actually succeeded.
- If a tool returns an error, explain the error clearly instead of pretending success.
- For multi-step workflows, plan the whole sequence mentally, but when you call a proposal tool submit only the current executable layer whose inputs already exist in project context.

Keep final answers concise and concrete.
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

    def answer(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> str:
        api_key = self._manager_api_key()
        if not api_key:
            raise ManagerPlanningError("Manager API key is not configured.")
        resolved_model = self.resolve_tool_model(self.settings.manager_model)
        context = self._build_context(snapshot, chat_request, extra_context)
        payload = {
            "model": resolved_model,
            "max_tokens": self.settings.manager_max_tokens,
            "temperature": self.settings.manager_temperature,
            "system": CHAT_SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(context, ensure_ascii=False, indent=2),
                        }
                    ],
                }
            ],
        }
        response_payload = self._post_messages(payload, resolved_model, api_key)
        text = self._extract_text(response_payload).strip()
        if not text:
            raise ManagerPlanningError("DeepSeek returned an empty chat response.")
        return text

    def agent_turn(self, messages: list[dict], tools: list[dict]) -> dict:
        api_key = self._manager_api_key()
        if not api_key:
            raise ManagerPlanningError("Manager API key is not configured.")
        resolved_model = self.resolve_tool_model(self.settings.manager_model)
        payload = {
            "model": resolved_model,
            "max_tokens": self.settings.manager_max_tokens,
            "temperature": self.settings.manager_temperature,
            "system": HARNESS_SYSTEM_PROMPT,
            "messages": messages,
            "tools": tools,
        }
        return self._post_messages(payload, resolved_model, api_key)

    def plan(self, snapshot: dict, chat_request: ChatRequest, extra_context: dict | None = None) -> ManagerPlanDraft:
        api_key = self._manager_api_key()
        if not api_key:
            raise ManagerPlanningError("Manager API key is not configured.")
        resolved_model = self.resolve_tool_model(self.settings.manager_model)

        payload = {
            "model": resolved_model,
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
            "tool_choice": {"type": "any"},
        }
        response_payload = self._post_messages(payload, resolved_model, api_key)
        tool_input = self._extract_tool_input(response_payload)
        try:
            draft = ManagerPlanDraft.model_validate(tool_input)
        except ValidationError as exc:
            raise ManagerPlanningError(f"DeepSeek returned an invalid manager plan: {exc}") from exc
        draft.ensure_valid()
        return draft

    def _post_messages(self, payload: dict, resolved_model: str, api_key: str) -> dict:
        endpoint = f"{self._manager_api_base_url().rstrip('/')}/v1/messages"
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
            raise ManagerPlanningError(
                self._build_http_error_message(
                    status_code=exc.code,
                    detail=detail,
                    configured_model=self.settings.manager_model,
                    resolved_model=resolved_model,
                )
            ) from exc
        except error.URLError as exc:
            raise ManagerPlanningError(f"DeepSeek request failed: {exc.reason}") from exc
        except (TimeoutError, OSError, client.HTTPException) as exc:
            raise ManagerPlanningError(f"DeepSeek request failed: {exc}") from exc

        try:
            response_payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManagerPlanningError("DeepSeek returned invalid JSON at the HTTP layer.") from exc
        return response_payload

    def _manager_api_key(self) -> str:
        value = self.settings.manager_api_key or self.settings.deepseek_api_key
        return value.get_secret_value() if value else ""

    def _manager_api_base_url(self) -> str:
        return self.settings.manager_api_base_url or self.settings.deepseek_api_base_url

    @staticmethod
    def resolve_tool_model(configured_model: str) -> str:
        model = configured_model.strip()
        return LEGACY_TOOL_MODEL_ALIASES.get(model, model)

    @staticmethod
    def _build_http_error_message(status_code: int, detail: str, configured_model: str, resolved_model: str) -> str:
        guidance = ""
        try:
            payload = json.loads(detail)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            error_payload = payload.get("error")
            message = error_payload.get("message", "") if isinstance(error_payload, dict) else ""
            if "does not support this tool_choice" in message:
                guidance = (
                    " Manager tool-use requests require a DeepSeek v4 model such as "
                    "`deepseek-v4-pro` or `deepseek-v4-flash`."
                )
        if configured_model != resolved_model:
            guidance += f" Configured model `{configured_model}` was normalized to `{resolved_model}`."
        return f"DeepSeek request failed with HTTP {status_code}: {detail}{guidance}"

    @staticmethod
    def _extract_tool_input(response_payload: dict) -> dict:
        return DeepSeekManagerPlanner._extract_named_tool_input(response_payload, "submit_manager_plan")

    @staticmethod
    def _extract_named_tool_input(response_payload: dict, tool_name: str) -> dict:
        content = response_payload.get("content", [])
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                tool_input = block.get("input")
                if isinstance(tool_input, dict):
                    return tool_input
        raise ManagerPlanningError(f"DeepSeek did not return the required {tool_name} tool call.")

    @staticmethod
    def _extract_text(response_payload: dict) -> str:
        parts: list[str] = []
        content = response_payload.get("content", [])
        for block in content:
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)

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
            "op_contracts": {
                "create_module": {
                    "target": "new_module",
                    "required_fields": ["module_id", "title"],
                    "optional_fields": ["status", "summary", "depends_on_assets", "expected_outputs", "linked_cards"],
                },
                "create_module_group": {
                    "target": "new_module_group",
                    "required_fields": ["module_id", "title"],
                    "optional_fields": ["status", "summary", "depends_on_assets", "expected_outputs", "linked_cards"],
                },
                "add_submodule": {
                    "target": "existing_module_group",
                    "required_fields": ["parent_module_id", "module_id", "title"],
                    "optional_fields": ["status"],
                },
                "create_card": {
                    "target": "new_card",
                    "required_fields": ["card_id", "card_type", "title", "status", "summary"],
                    "optional_fields": [
                        "why",
                        "inputs",
                        "outputs",
                        "key_findings",
                        "manager_review",
                        "next_actions",
                        "linked_modules",
                        "linked_runs",
                        "linked_assets",
                        "progress_note",
                        "executor_context",
                    ],
                },
                "update_card": {
                    "target": "existing_card",
                    "required_fields": ["card_id"],
                    "optional_fields": [
                        "title",
                        "status",
                        "summary",
                        "why",
                        "inputs",
                        "outputs",
                        "key_findings",
                        "manager_review",
                        "next_actions",
                        "linked_modules",
                        "linked_assets",
                        "progress_note",
                        "executor_context",
                    ],
                },
                "update_module": {
                    "target": "existing_module",
                    "required_fields": ["module_id"],
                    "optional_fields": ["title", "status", "summary", "depends_on_assets", "expected_outputs", "linked_cards"],
                },
                "set_card_status": {
                    "target": "existing_card",
                    "required_fields": ["card_id", "status"],
                    "optional_fields": [],
                },
                "set_module_status": {
                    "target": "existing_module",
                    "required_fields": ["module_id", "status"],
                    "optional_fields": [],
                },
                "create_run": {
                    "target": "new_run",
                    "required_fields": ["run_id", "card_id", "status", "title", "summary", "started_at"],
                    "optional_fields": ["module_id", "finished_at", "worker_type"],
                },
                "mark_downstream_stale": {
                    "target": "existing_assets",
                    "required_fields": ["asset_ids"],
                    "optional_fields": [],
                },
            },
            "cards": [
                {
                    "card_id": card.card_id,
                    "card_type": card.card_type,
                    "title": card.title,
                    "status": card.status,
                    "summary": card.summary,
                    "why": card.why,
                    "inputs": [item.model_dump() for item in card.inputs],
                    "outputs": [item.model_dump() for item in card.outputs],
                    "key_findings": list(card.key_findings),
                    "manager_review": card.manager_review,
                    "next_actions": list(card.next_actions),
                    "linked_modules": card.linked_modules,
                    "linked_runs": card.linked_runs,
                    "linked_assets": card.linked_assets,
                    "progress_note": card.progress_note,
                    "executor_context": card.executor_context.model_dump() if card.executor_context else None,
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
            "module_groups": [
                {
                    "module_id": module.module_id,
                    "title": module.title,
                    "submodules": [item.model_dump() for item in module.submodules],
                }
                for module in graph.modules
                if module.type == "module_group"
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
        context["script_preference_guidance"] = self._script_preference_guidance(chat_request.context.script_preference)
        context["runtime_preference_guidance"] = self._runtime_preference_guidance(
            chat_request.context.python_runtime,
            chat_request.context.r_runtime,
        )
        if extra_context:
            context["extra_context"] = extra_context
        return context

    @staticmethod
    def _script_preference_guidance(script_preference: str) -> dict:
        normalized = script_preference if script_preference in {"auto", "prefer_python", "prefer_r", "prefer_mixed"} else "auto"
        instructions = {
            "auto": (
                "No script language preference is set. If creating new bioinformatics analysis cards and Python vs R materially "
                "changes implementation quality or runtime dependency choices, ask the user which script style they prefer."
            ),
            "prefer_python": (
                "Soft script preference: prefer Python scripts when practical. This is not a hard constraint; use R when it is "
                "more reliable or better supported for this task."
            ),
            "prefer_r": (
                "Soft script preference: prefer R scripts when practical. This is not a hard constraint; use Python when it is "
                "more reliable or better supported for this task."
            ),
            "prefer_mixed": (
                "Soft script preference: choose Python or R per task based on reliability, available runtime dependencies, and "
                "clearer reproducible code."
            ),
        }
        return {
            "value": normalized,
            "card_instruction_block": instructions[normalized],
            "hard_constraint": False,
        }

    @staticmethod
    def _runtime_preference_guidance(python_runtime: str | None, r_runtime: str | None) -> dict:
        instructions = []
        if python_runtime:
            instructions.append(f"Preferred Python runtime for future card execution: {python_runtime}.")
        if r_runtime:
            instructions.append(f"Preferred R runtime for future card execution: {r_runtime}.")
        return {
            "python_runtime": python_runtime,
            "r_runtime": r_runtime,
            "card_instruction_block": (
                f"Runtime preference: {' '.join(instructions)} Add this to executor_context.instruction_blocks "
                "when it is relevant to a new or updated analysis card."
                if instructions
                else None
            ),
        }
