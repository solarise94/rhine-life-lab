import { Agent } from "@earendil-works/pi-agent-core";
import { clampThinkingLevel, getModel, registerBuiltInApiProviders, Type } from "@earendil-works/pi-ai";

registerBuiltInApiProviders();

const HOST = process.env.MANAGER_AGENT_HOST || "127.0.0.1";
const PORT = Number(process.env.MANAGER_AGENT_PORT || "18002");
const PROVIDER = process.env.MANAGER_AGENT_PROVIDER || "deepseek";
const MODEL = process.env.MANAGER_AGENT_MODEL || process.env.BLUEPRINT_MANAGER_MODEL || "deepseek-v4-pro";
const API_KEY = process.env.MANAGER_AGENT_API_KEY || process.env.BLUEPRINT_DEEPSEEK_API_KEY || "";
const TIMEOUT_MS = Number(process.env.MANAGER_AGENT_TIMEOUT_MS || "600000");
const HEARTBEAT_INTERVAL_MS = 5000;

const SYSTEM_PROMPT = `You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

You are an interactive chat agent. Most messages should be answered directly. Use tools only when project context is required, result assets must be read, or the user asks to plan/change the blueprint.

Hard permissions:
- You may read blueprint context through tools.
- You may create auditable proposals through tools.
- You may modify existing blueprint proposals through proposal tools.
- You may delete blueprint modules only by creating a proposal that marks modules/cards cancelled.
- You may delete blueprint cards only by creating a proposal that marks the card cancelled.
- You may restore cancelled blueprint modules only by creating a proposal.
- You may restore cancelled blueprint cards only by creating a proposal.
- You may read result assets through read_result_asset, but not arbitrary files.
- You must never claim a change was applied. The user must accept a proposal in the UI.
- You must not run shell commands, write scripts, execute analyses, or edit files.
- If a tool fails validation, inspect the error, correct your arguments, and retry when possible.

Blueprint mutation rules:
- For complete workflow requests, call plan_blueprint first to create a read-only multi-layer plan, then call review_blueprint_plan before any proposal tool. These tools do not change the blueprint.
- For add/update proposals, call draft_blueprint_proposal with valid patch ops.
- For delete, prefer delete_blueprint_module with the exact module_id from context.
- For restore, prefer restore_blueprint_module with the exact module_id from context.
- For proposal edits, call modify_blueprint_proposal with the exact proposal_id.
- For result interpretation, call read_result_asset with the exact asset_id from context.
- Do not invent existing ids. Call get_project_context first if ids are uncertain.
- If a relevant module/card already exists in project context, reuse its exact module_id/card_id and update or restore it instead of creating a duplicate.
- Duplicate module_id/card_id proposals are invalid. Reconcile with the current graph state, not with your memory of earlier chat text.
- Keep final replies concise and user-facing.

Tool-use contract:
- For ordinary greetings, chat, explanation, brainstorming, or analysis suggestions, answer in text without tools.
- Do not create a proposal unless the user asks to add, modify, delete, restore, rerun, rollback, or otherwise change the blueprint.
- If the user asks about the current project state and the needed context is not in the message, call get_project_context before answering.
- If the user asks to change an existing module but does not provide an exact module_id/card_id, call get_project_context first, then choose the exact id.
- For multi-step workflow creation, call plan_blueprint and review_blueprint_plan before any proposal tool. Explain plan review errors if the reviewer blocks the plan. If the reviewer approves it, draft only the next currently executable layer when the user asked to build the blueprint.
- Do not draft downstream cards whose inputs are expected to come from outputs planned in the same proposal. Wait until upstream assets exist before proposing the next layer.
- Read the proposal tool response carefully. If it reports insufficient assets or downstream-layer violations, split the workflow and retry with only the current layer.
- Proposal-building tool calls must be sequential. Do not dump the entire workflow into draft_blueprint_proposal; use plan_blueprint and review_blueprint_plan for the complete route and draft_blueprint_proposal for one executable layer.
- If the project already contains a partial or previously accepted version of the requested analysis, continue from that existing graph state instead of creating a second module/card for the same analysis.
- For deletion/cancellation, do not call draft_blueprint_proposal directly unless the dedicated delete tool cannot express the request. Prefer delete_blueprint_module.
- For restoration, do not call draft_blueprint_proposal directly unless the dedicated restore tool cannot express the request. Prefer restore_blueprint_module.
- Tool calls that create or modify proposals must be the last meaningful action unless the tool returns a validation error. plan_blueprint and review_blueprint_plan are read-only and may be followed by a proposal tool. After a successful proposal tool call, explain the proposal and remind the user that they must accept it in the UI.
- If backend validation fails, read the error, correct the arguments, and retry at most twice. If still failing, explain the blocker clearly.

Patch op guide for draft_blueprint_proposal / modify_blueprint_proposal:
- create_module payload: { module_id, title, status, summary, depends_on_assets, expected_outputs, linked_cards }
- update_module payload: { module_id, title?, status?, summary?, depends_on_assets?, expected_outputs?, linked_cards? }
- create_card payload must include a complete user-facing card: { card_id, card_type: "module", title, status, summary, why, inputs, outputs, key_findings, manager_review, next_actions, linked_modules, linked_runs, linked_assets }
- update_card payload must include card_id and only editable fields such as title, status, summary, why, inputs, outputs, key_findings, manager_review, next_actions, linked_modules, linked_assets. Do not set linked_runs or technical_refs.
- add_submodule payload: { parent_module_id, module_id, title, status }
- set_module_status payload: { module_id, status }
- set_card_status payload: { card_id, status }
- attach_asset_to_card payload: { card_id, asset_id }
- attach_run_to_card payload: { card_id, run_id }
- Use stable lowercase ids such as module_go_enrichment and card_go_enrichment. Reuse existing ids when updating; create new ids only for new modules/cards.
- Inputs and outputs on cards are arrays of refs shaped like { label, asset_id?, status? }. Use existing asset_id values from context when available.
- When planning new outputs that do not exist yet, assign stable planned asset ids in plan_blueprint and make downstream inputs reuse those ids in the plan. Do not submit downstream cards until those upstream assets are available in project context.
- Prefer status "planned" for newly accepted future work, "proposed" only for speculative items waiting on user decision, "cancelled" for deleted/dormant items, and "accepted" only for completed accepted work.`;

const PLAN_BLUEPRINT_DESCRIPTION = `Create a read-only multi-layer blueprint workflow plan. This tool never writes modules, cards, assets, patches, or proposals.

Use this before draft_blueprint_proposal when the user asks for a complete workflow, a full analysis blueprint, or multiple dependent analysis stages. The plan can include downstream steps and planned outputs, but it is not an auditable proposal and does not change the graph.

Required behavior:
- Call get_project_context first if uploaded assets, existing modules/cards, or current project state matter.
- Put steps in execution order.
- Give each step stable step_id, module_id, card_id, input_assets, output_assets, and depends_on_step_ids.
- Reuse stable planned output asset ids as downstream inputs inside the plan.
- After this tool returns, call review_blueprint_plan. Only after reviewer approval may you draft the next executable step.
- If no step is currently executable, explain what input asset or metadata is missing instead of creating a proposal.`;

const REVIEW_BLUEPRINT_PLAN_DESCRIPTION = `Deterministically review a blueprint plan. This tool never writes modules, cards, assets, patches, or proposals.

Use this immediately after plan_blueprint and before any proposal tool. The reviewer ignores card prose and checks only structured fields: step_id, module_id, card_id, input_assets.asset_id, output_assets.asset_id, depends_on_step_ids, and current graph assets.

Required behavior:
- Pass the plan object returned by plan_blueprint as { plan }.
- If approved is false, explain the returned errors and revise the plan before drafting a proposal.
- If approved is true, use next_executable_step_id to decide which single step may be converted into draft_blueprint_proposal.
- Do not create a proposal for a step whose block_reasons are non-empty.`;

const DRAFT_BLUEPRINT_PROPOSAL_DESCRIPTION = `Create an auditable proposal for adding or modifying blueprint modules/cards. This never applies the change.

Use this for add/update proposals when no dedicated tool is more appropriate. The backend validates ops before saving.

Required behavior:
- Use get_project_context first if you need ids, existing assets, existing cards, or open proposals.
- Include both module-level and card-level ops for a new module.
- If a matching module/card already exists, do not create another one. Use update_module/update_card or the restore/delete tools with the exact existing ids.
- Do not claim the change was applied.
- Keep title/summary/impact_summary user-facing.
- Submit only one executable layer per tool call. If a downstream step depends on outputs from a new upstream step, stop and propose only the upstream step first.
- The tool response may include asset sufficiency diagnostics. If it says assets are insufficient or the proposal spans downstream layers, split the workflow and retry with the current layer only.

Common add_module ops:
1. create_module with module_id, title, status, summary, depends_on_assets, expected_outputs, linked_cards.
2. optionally add_submodule if the new module belongs inside an existing group.
3. create_card with complete card fields and linked_modules pointing to the module.

Common update ops:
1. update_module for module metadata/status/dependencies.
2. update_card for user-facing card text/status/inputs/outputs.

Do not use this tool for simple deletion/restoration when delete_blueprint_module or restore_blueprint_module can do it.`;

const MODIFY_BLUEPRINT_PROPOSAL_DESCRIPTION = `Replace an existing proposal with a validated structured proposal draft. This updates the proposal only and never applies it.

Use this only when the user is editing an existing proposed proposal. Call get_project_context first if proposal_id is uncertain. Preserve the user's requested change and regenerate a complete valid proposal body, including title, summary, impact_summary, patch_type, reason, and ops.`;

const TOOL_STATUS_LABELS = {
  get_project_context: {
    active: "正在查看蓝图",
    done: "已查看蓝图",
  },
  plan_blueprint: {
    active: "正在规划蓝图",
    done: "已规划蓝图",
  },
  review_blueprint_plan: {
    active: "正在审查蓝图计划",
    done: "已审查蓝图计划",
  },
  draft_blueprint_proposal: {
    active: "正在生成蓝图提案",
    done: "已生成蓝图提案",
  },
  delete_blueprint_module: {
    active: "正在生成删除模块提案",
    done: "已生成删除模块提案",
  },
  delete_blueprint_card: {
    active: "正在生成删除卡片提案",
    done: "已生成删除卡片提案",
  },
  restore_blueprint_module: {
    active: "正在生成恢复模块提案",
    done: "已生成恢复模块提案",
  },
  restore_blueprint_card: {
    active: "正在生成恢复卡片提案",
    done: "已生成恢复卡片提案",
  },
  modify_blueprint_proposal: {
    active: "正在修改提案",
    done: "已修改提案",
  },
  read_result_asset: {
    active: "正在读取结果文件",
    done: "已读取结果文件",
  },
};

function jsonResponse(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
}

function openSse(res) {
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
  });
  res.flushHeaders?.();
}

function writeSseEvent(res, payload) {
  res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  if (!chunks.length) {
    return {};
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
}

async function callBackend(baseUrl, token, path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: options.method || "GET",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { detail: text };
  }
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload);
    throw new Error(detail || `Backend tool failed with HTTP ${response.status}`);
  }
  return payload;
}

function textResult(text, details = {}, terminate = false) {
  return {
    content: [{ type: "text", text }],
    details,
    terminate,
  };
}

function createTools(request) {
  const { project_id: projectId, backend_api_base_url: baseUrl, internal_tool_token: token } = request;
  return [
    {
      name: "get_project_context",
      label: "Read project context",
      description: "Read the current Blueprint project, modules, cards, assets, runs, and proposals. This is read-only. Use before referencing ids, modifying existing blueprint entities, reading assets, deleting/restoring modules, or modifying open proposals.",
      parameters: Type.Object({}),
      execute: async (_toolCallId, _params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/context`, { signal });
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
    },
    {
      name: "plan_blueprint",
      label: "Plan blueprint workflow",
      description: PLAN_BLUEPRINT_DESCRIPTION,
      parameters: Type.Object({
        objective: Type.String(),
        assumptions: Type.Optional(Type.Array(Type.String())),
        steps: Type.Array(Type.Record(Type.String(), Type.Any())),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/plan-blueprint`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
      executionMode: "sequential",
    },
    {
      name: "review_blueprint_plan",
      label: "Review blueprint plan",
      description: REVIEW_BLUEPRINT_PLAN_DESCRIPTION,
      parameters: Type.Object({
        plan: Type.Record(Type.String(), Type.Any()),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/review-blueprint-plan`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
      executionMode: "sequential",
    },
    {
      name: "draft_blueprint_proposal",
      label: "Draft blueprint proposal",
      description: DRAFT_BLUEPRINT_PROPOSAL_DESCRIPTION,
      parameters: Type.Object({
        title: Type.String(),
        summary: Type.String(),
        impact_summary: Type.String(),
        patch_type: Type.Union([
          Type.Literal("add_module"),
          Type.Literal("add_module_group"),
          Type.Literal("update_card"),
          Type.Literal("delete_module"),
          Type.Literal("review_run"),
          Type.Literal("semantic_rollback"),
        ]),
        reason: Type.String(),
        message: Type.Optional(Type.String()),
        ops: Type.Array(
          Type.Object({
            op: Type.String(),
            payload: Type.Record(Type.String(), Type.Any()),
          }),
        ),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/proposals`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
      executionMode: "sequential",
    },
    {
      name: "delete_blueprint_module",
      label: "Draft module deletion",
      description: "Create an auditable proposal to delete/cancel a module by exact module_id. This never applies the change. Use get_project_context first unless the module_id is already certain. The module/card becomes cancelled/dormant after user acceptance; do not describe it as permanently erased.",
      parameters: Type.Object({
        module_id: Type.String(),
        reason: Type.String(),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/delete-module`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
      executionMode: "sequential",
    },
    {
      name: "delete_blueprint_card",
      label: "Draft card deletion",
      description: "Create an auditable proposal to delete/cancel a specialist card by exact card_id. This never applies the change. Use get_project_context first unless the card_id is already certain. The card becomes cancelled/dormant after user acceptance; do not describe it as permanently erased.",
      parameters: Type.Object({
        card_id: Type.Optional(Type.String()),
        module_id: Type.Optional(Type.String()),
        reason: Type.String(),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/delete-card`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
      executionMode: "sequential",
    },
    {
      name: "restore_blueprint_module",
      label: "Draft module restore",
      description: "Create an auditable proposal to restore a cancelled module/card by exact module_id. This never applies the change. Use get_project_context first unless the module_id is already certain. Choose status planned unless the user explicitly wants it to remain proposed.",
      parameters: Type.Object({
        module_id: Type.String(),
        reason: Type.String(),
        status: Type.Optional(Type.Union([Type.Literal("planned"), Type.Literal("proposed")])),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/restore-module`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
      executionMode: "sequential",
    },
    {
      name: "restore_blueprint_card",
      label: "Draft card restore",
      description: "Create an auditable proposal to restore a cancelled specialist card by exact card_id. This never applies the change. Use get_project_context first unless the card_id is already certain. Choose status planned unless the user explicitly wants it to remain proposed.",
      parameters: Type.Object({
        card_id: Type.Optional(Type.String()),
        module_id: Type.Optional(Type.String()),
        reason: Type.String(),
        status: Type.Optional(Type.Union([Type.Literal("planned"), Type.Literal("proposed")])),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/restore-card`, {
          method: "POST",
          body: params,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
      executionMode: "sequential",
    },
    {
      name: "modify_blueprint_proposal",
      label: "Modify blueprint proposal",
      description: MODIFY_BLUEPRINT_PROPOSAL_DESCRIPTION,
      parameters: Type.Object({
        proposal_id: Type.String(),
        message: Type.String(),
        title: Type.String(),
        summary: Type.String(),
        impact_summary: Type.String(),
        patch_type: Type.Union([
          Type.Literal("add_module"),
          Type.Literal("add_module_group"),
          Type.Literal("update_card"),
          Type.Literal("delete_module"),
          Type.Literal("review_run"),
          Type.Literal("semantic_rollback"),
        ]),
        reason: Type.String(),
        ops: Type.Array(
          Type.Object({
            op: Type.String(),
            payload: Type.Record(Type.String(), Type.Any()),
          }),
        ),
      }),
      execute: async (_toolCallId, params, signal) => {
        const { proposal_id: proposalId, ...body } = params;
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/proposals/${proposalId}/modify`, {
          method: "POST",
          body,
          signal,
        });
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
      executionMode: "sequential",
    },
    {
      name: "read_result_asset",
      label: "Read result asset",
      description: "Read a whitelisted result asset detail/preview by exact asset_id. This is read-only and cannot read arbitrary paths. Use get_project_context first if the asset_id is uncertain. Use this for interpreting result files before answering or before proposing downstream changes based on a result.",
      parameters: Type.Object({
        asset_id: Type.String(),
      }),
      execute: async (_toolCallId, params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/assets/${params.asset_id}`, { signal });
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
    },
  ];
}

function extractText(message) {
  if (!message || !Array.isArray(message.content)) {
    return "";
  }
  return message.content
    .filter((item) => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("");
}

function latestToolProposal(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type === "tool_execution_end" && !event.isError) {
      const details = event.result?.details;
      if (details?.proposal) {
        return details;
      }
    }
  }
  return null;
}

function toolStatusLabel(toolName) {
  return TOOL_STATUS_LABELS[toolName] || {
    active: `正在使用 ${toolName}`,
    done: `已完成 ${toolName}`,
  };
}

function collectThinking(thinkingBlocks) {
  return Array.from(thinkingBlocks.entries())
    .sort((left, right) => left[0] - right[0])
    .map((entry) => entry[1]?.trim())
    .filter(Boolean)
    .join("\n\n");
}

function normalizeHistory(messages) {
  if (!Array.isArray(messages)) {
    return [];
  }
  return messages
    .filter((message) => message && (message.role === "user" || message.role === "manager") && typeof message.content === "string")
    .map((message) => ({
      role: message.role === "manager" ? "assistant" : "user",
      content: [{ type: "text", text: message.content }],
      timestamp: Date.now(),
    }));
}

function mapThinkingLevel(thinkingEffort, model) {
  // DeepSeek V4 Pro only exposes off/high/xhigh in pi-ai. Map the UI's
  // low/medium/high controls onto distinct model-supported levels so the
  // selector produces an actual behavioral difference.
  const requestedLevel =
    thinkingEffort === "low"
      ? "off"
      : thinkingEffort === "high"
        ? "xhigh"
        : "high";
  return clampThinkingLevel(model, requestedLevel);
}

async function runManagerChat(payload, emitEvent = null, externalAbortSignal = null) {
  if (!API_KEY) {
    throw new Error("MANAGER_AGENT_API_KEY or BLUEPRINT_DEEPSEEK_API_KEY is not configured.");
  }
  const model = getModel(PROVIDER, MODEL);
  if (!model) {
    throw new Error(`Manager model not found: provider=${PROVIDER}, model=${MODEL}`);
  }
  const events = [];
  let finalAssistantMessage = null;
  let proposalPayload = null;
  let streamedText = "";
  const thinkingBlocks = new Map();
  let lastEmitAt = Date.now();
  let syntheticThinkingOpen = false;
  const emit = (event) => {
    lastEmitAt = Date.now();
    emitEvent?.(event);
  };
  const agent = new Agent({
    initialState: {
      systemPrompt: SYSTEM_PROMPT,
      model,
      thinkingLevel: mapThinkingLevel(payload.thinking_effort, model),
      tools: createTools(payload),
      messages: normalizeHistory(payload.messages),
    },
    getApiKey: () => API_KEY,
    toolExecution: "sequential",
    transport: "auto",
    maxRetryDelayMs: 60000,
    transformContext: async (messages) => messages.slice(-30),
  });
  agent.subscribe((event) => {
    events.push(event);
    if (event.type === "message_update" && event.assistantMessageEvent) {
      const assistantEvent = event.assistantMessageEvent;
      if (assistantEvent.type === "thinking_start") {
        if (!thinkingBlocks.has(assistantEvent.contentIndex)) {
          thinkingBlocks.set(assistantEvent.contentIndex, "");
        }
        emit({
          type: "thinking_start",
          content_index: assistantEvent.contentIndex,
        });
      } else if (assistantEvent.type === "thinking_delta") {
        const nextThinking = `${thinkingBlocks.get(assistantEvent.contentIndex) || ""}${assistantEvent.delta || ""}`;
        thinkingBlocks.set(assistantEvent.contentIndex, nextThinking);
        emit({
          type: "thinking_delta",
          delta: assistantEvent.delta || "",
          content_index: assistantEvent.contentIndex,
        });
      } else if (assistantEvent.type === "thinking_end") {
        const finalizedThinking =
          typeof assistantEvent.content === "string"
            ? assistantEvent.content
            : thinkingBlocks.get(assistantEvent.contentIndex) || "";
        thinkingBlocks.set(assistantEvent.contentIndex, finalizedThinking);
        emit({
          type: "thinking_end",
          content: finalizedThinking,
          content_index: assistantEvent.contentIndex,
        });
      } else if (assistantEvent.type === "text_delta") {
        const delta = assistantEvent.delta || "";
        streamedText += delta;
        emit({
          type: "text_delta",
          delta,
          content_index: assistantEvent.contentIndex,
        });
      }
    }
    if (event.type === "tool_execution_start") {
      const label = toolStatusLabel(event.toolName);
      emit({
        type: "tool_start",
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
        label: label.active,
        done_label: label.done,
      });
    }
    if (event.type === "tool_execution_end") {
      const label = toolStatusLabel(event.toolName);
      const details = event.result?.details;
      emit({
        type: "tool_end",
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
        label: event.isError ? `${label.active}失败` : label.done,
        done_label: label.done,
        is_error: Boolean(event.isError),
      });
      if (!event.isError && details?.proposal) {
        proposalPayload = details;
        emit({
          type: "proposal",
          proposal: details.proposal,
        });
      }
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      finalAssistantMessage = event.message;
    }
  });
  const userEnvelope = {
    user_request: payload.message,
    selected_context: payload.context || {},
    instruction: "Answer naturally. Use tools only when the user asks about current project state, result assets, blueprint planning, or blueprint changes. For complete blueprint requests, call plan_blueprint then review_blueprint_plan first; for actual graph changes, use proposal tools only.",
  };
  const abortController = new AbortController();
  const timeoutId = setTimeout(() => abortController.abort(new Error("manager_timeout")), TIMEOUT_MS);
  const heartbeatId =
    emitEvent &&
    setInterval(() => {
      if (Date.now() - lastEmitAt < HEARTBEAT_INTERVAL_MS) {
        return;
      }
      if (!syntheticThinkingOpen) {
        syntheticThinkingOpen = true;
        emit({
          type: "thinking_start",
          content_index: -1,
        });
      }
      emit({
        type: "heartbeat",
        stage: "waiting_for_model",
        message: "Manager 正在生成回复…",
      });
    }, HEARTBEAT_INTERVAL_MS);
  const abort = abortController.signal;
  const run = agent.prompt(JSON.stringify(userEnvelope, null, 2));
  if (externalAbortSignal) {
    if (externalAbortSignal.aborted) {
      abortController.abort(externalAbortSignal.reason);
    } else {
      externalAbortSignal.addEventListener("abort", () => abortController.abort(externalAbortSignal.reason), { once: true });
    }
  }
  abort.addEventListener("abort", () => agent.abort(), { once: true });
  try {
    await run;
  } finally {
    clearTimeout(timeoutId);
    if (heartbeatId) {
      clearInterval(heartbeatId);
    }
  }
  proposalPayload = proposalPayload || latestToolProposal(events);
  const thinking = collectThinking(thinkingBlocks) || undefined;
  if (proposalPayload) {
    const fallbackMessage = proposalPayload.message || proposalPayload.proposal?.summary || "已生成可审核 proposal。";
    return {
      message: streamedText.trim() || extractText(finalAssistantMessage).trim() || fallbackMessage,
      thinking,
      proposal: proposalPayload.proposal || null,
      actions: proposalPayload.actions || [],
      warnings: proposalPayload.warnings || [],
    };
  }
  const text = streamedText.trim() || extractText(finalAssistantMessage).trim();
  const stopReason = finalAssistantMessage?.stopReason;
  if (stopReason === "error" || stopReason === "aborted") {
    throw new Error(finalAssistantMessage.errorMessage || `Manager agent stopped with ${stopReason}`);
  }
  if (!text) {
    throw new Error("Manager agent returned an empty response.");
  }
  return { message: text, thinking, proposal: null, actions: [], warnings: [] };
}

async function handle(req, res) {
  const { pathname } = new URL(req.url || "/", `http://${req.headers.host || "127.0.0.1"}`);
  if (req.method === "GET" && pathname === "/healthz") {
    jsonResponse(res, 200, { status: "ok" });
    return;
  }
  if (req.method === "POST" && pathname === "/chat-stream") {
    const disconnectController = new AbortController();
    req.on("close", () => disconnectController.abort(new Error("client_disconnected")));
    openSse(res);
    try {
      const payload = await readJson(req);
      const response = await runManagerChat(payload, (event) => writeSseEvent(res, event), disconnectController.signal);
      writeSseEvent(res, { type: "response", response });
      writeSseEvent(res, { type: "done" });
    } catch (error) {
      writeSseEvent(res, { type: "error", detail: error instanceof Error ? error.message : String(error) });
    } finally {
      res.end();
    }
    return;
  }
  if (req.method !== "POST" || pathname !== "/chat") {
    jsonResponse(res, 404, { detail: "Not found" });
    return;
  }
  try {
    const payload = await readJson(req);
    const response = await runManagerChat(payload);
    jsonResponse(res, 200, response);
  } catch (error) {
    jsonResponse(res, 502, { detail: error instanceof Error ? error.message : String(error) });
  }
}

const server = (await import("node:http")).createServer((req, res) => {
  void handle(req, res);
});

server.listen(PORT, HOST, () => {
  console.log(`Blueprint Manager Pi agent listening on http://${HOST}:${PORT}`);
});
