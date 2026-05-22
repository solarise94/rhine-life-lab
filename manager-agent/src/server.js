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
const WAIT_LOG_INTERVAL_MS = Number(process.env.MANAGER_AGENT_WAIT_LOG_INTERVAL_MS || "30000");

const SYSTEM_PROMPT = `You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

You are an interactive project agent. Answer directly when the user is asking a general question. Use tools when they materially improve correctness or when the user asks you to inspect or change the current project.

Core model:
- The blueprint is represented by cards. A card is the editable unit of the blueprint.
- Data assets are referenced by asset_id. Card outputs are expected/planned assets; later card inputs can reuse those asset_ids.
- Card step is the timeline layer. A card must be later than the assets it consumes.

Available capabilities:
- get_project_context reads current cards, modules, assets, runs, and claims.
- list_data_assets reads materialized assets, planned outputs, workspace paths, producer/consumer relations, and timeline steps.
- create_card, update_card, and delete_card directly modify blueprint cards after backend validation.
- read_result_asset reads a whitelisted result asset preview by asset_id.

Judgment:
- Decide whether current project context is needed. If exact card ids, asset ids, steps, or current blueprint state matter, inspect the project first.
- For broad workflow additions, inspect data assets/timeline before choosing steps and asset_ids.
- For simple conceptual questions, answer without tools.
- For blueprint/card changes, use card tools directly once you have enough context. Do not describe a change as complete unless a write tool succeeded.
- If a write tool returns ok:false, use the message/retry_hint to correct arguments and retry when the correction is clear. If it is not clear, inspect context or ask a focused question.
- For multi-step workflow creation, you may create multiple cards in one conversation. Re-check the timeline when useful.
- Reuse existing card ids when updating existing work. Create new ids only for genuinely new cards.
- Do not use or mention blueprint proposal, blueprint review, or approval flows. Card tools are the source of truth for blueprint edits.
- Respect selected_context.script_preference when creating analysis cards. It is a soft script-language preference, not a hard constraint.
- If script_preference is auto and a new bioinformatics card could reasonably be implemented in either Python or R, ask the user which script style they prefer when that choice materially affects the workflow.
- When a concrete script preference is known, add it to executor_context.instruction_blocks on new or updated analysis cards.
- Keep final replies concise and user-facing.

Card fields:
- Required for create_card: card_id, card_type, title, status, summary.
- Common card_type values: module, module_group.
- Common status values: planned, proposed, accepted, cancelled, failed, stale, superseded.
- Useful fields: step, why, inputs, outputs, key_findings, manager_review, next_actions, linked_modules, linked_runs, linked_assets, progress_note.
- executor_context may include instruction_blocks for soft execution guidance such as script-language preference.
- Inputs and outputs are arrays shaped like { label, asset_id?, status? }.
- Prefer status "planned" for future work, "cancelled" for dormant/deleted cards, and "accepted" only for completed accepted work.`;

const TOOL_STATUS_LABELS = {
  get_project_context: {
    active: "正在查看蓝图",
    done: "已查看蓝图",
  },
  list_data_assets: {
    active: "正在查看数据资产",
    done: "已查看数据资产",
  },
  create_card: {
    active: "正在创建卡片",
    done: "已创建卡片",
  },
  update_card: {
    active: "正在更新卡片",
    done: "已更新卡片",
  },
  delete_card: {
    active: "正在删除卡片",
    done: "已删除卡片",
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

function toolErrorResult(error, context = {}) {
  const message = error instanceof Error ? error.message : String(error);
  const payload = {
    ok: false,
    error_type: context.error_type || "tool_error",
    message,
    retry_hint: context.retry_hint || retryHintForToolError(message),
    should_retry: context.should_retry ?? isRetryableToolError(message),
    ...context,
  };
  return textResult(JSON.stringify(payload, null, 2), payload, false);
}

function isRetryableToolError(message) {
  return /step too early|input asset .*missing|duplicate card_id|duplicate planned output|card not found|asset_id|required|title is required|summary is required/i.test(message);
}

function retryHintForToolError(message) {
  if (/step too early/i.test(message)) {
    return "Increase the card step to the minimum required by the error, then retry the same write tool.";
  }
  if (/input asset .*missing|asset_id/i.test(message)) {
    return "Call list_data_assets to find the correct asset_id, or create an upstream card output first.";
  }
  if (/duplicate card_id/i.test(message)) {
    return "Use update_card for the existing card, or choose a new card_id for genuinely new work.";
  }
  if (/duplicate planned output/i.test(message)) {
    return "Reuse the existing planned asset_id as an input, or choose a distinct output asset_id.";
  }
  if (/card not found/i.test(message)) {
    return "Call get_project_context to find the current card_id before retrying.";
  }
  if (/title is required|summary is required|required/i.test(message)) {
    return "Fill the required card fields and retry.";
  }
  return "Inspect the error and retry with corrected arguments if the correction is clear.";
}

function scriptPreferenceGuidance(scriptPreference) {
  const value = ["prefer_python", "prefer_r", "prefer_mixed", "auto"].includes(scriptPreference) ? scriptPreference : "auto";
  const instructions = {
    auto:
      "No script language preference is set. If creating new bioinformatics analysis cards and Python vs R materially changes implementation quality or runtime dependency choices, ask the user which script style they prefer.",
    prefer_python:
      "Soft script preference: prefer Python scripts when practical. This is not a hard constraint; use R when it is more reliable or better supported for this task.",
    prefer_r:
      "Soft script preference: prefer R scripts when practical. This is not a hard constraint; use Python when it is more reliable or better supported for this task.",
    prefer_mixed:
      "Soft script preference: choose Python or R per task based on reliability, available runtime dependencies, and clearer reproducible code.",
  };
  return {
    value,
    card_instruction_block: instructions[value],
    hard_constraint: false,
  };
}

function logManagerEvent(event, fields = {}) {
  console.log(
    JSON.stringify({
      ts: new Date().toISOString(),
      scope: "manager-agent",
      event,
      ...fields,
    }),
  );
}

function payloadSize(payload) {
  try {
    return Buffer.byteLength(JSON.stringify(payload ?? {}));
  } catch {
    return null;
  }
}

function summarizeToolPayload(toolName, payload) {
  if (!payload || typeof payload !== "object") {
    return {};
  }
  if (toolName === "get_project_context") {
    return {
      cards: Array.isArray(payload.cards) ? payload.cards.length : undefined,
      modules: Array.isArray(payload.modules) ? payload.modules.length : undefined,
      assets: Array.isArray(payload.assets) ? payload.assets.length : undefined,
    };
  }
  if (toolName === "list_data_assets") {
    return {
      assets: Array.isArray(payload.assets) ? payload.assets.length : undefined,
      cards: Array.isArray(payload.cards) ? payload.cards.length : undefined,
      planned_assets: Array.isArray(payload.planned_assets) ? payload.planned_assets.length : undefined,
    };
  }
  if (toolName === "create_card" || toolName === "update_card" || toolName === "delete_card") {
    return {
      card_id: payload.card?.card_id,
      card_status: payload.card?.status,
      card_step: payload.card?.step,
      timeline_cards: Array.isArray(payload.timeline?.cards) ? payload.timeline.cards.length : undefined,
      timeline_assets: Array.isArray(payload.timeline?.assets) ? payload.timeline.assets.length : undefined,
    };
  }
  if (payload.asset) {
    return {
      asset_id: payload.asset.asset_id,
      preview_kind: payload.preview?.kind,
      size_bytes: payload.preview?.size_bytes,
    };
  }
  return {};
}

async function callLoggedTool(toolName, toolCallId, projectId, baseUrl, token, path, options = {}, signal) {
  const startedAt = Date.now();
  logManagerEvent("tool_backend_start", {
    project_id: projectId,
    tool_name: toolName,
    tool_call_id: toolCallId,
    method: options.method || "GET",
    path,
    request_bytes: payloadSize(options.body),
  });
  try {
    const payload = await callBackend(baseUrl, token, path, { ...options, signal });
    logManagerEvent("tool_backend_done", {
      project_id: projectId,
      tool_name: toolName,
      tool_call_id: toolCallId,
      duration_ms: Date.now() - startedAt,
      response_bytes: payloadSize(payload),
      ...summarizeToolPayload(toolName, payload),
    });
    return payload;
  } catch (error) {
    logManagerEvent("tool_backend_error", {
      project_id: projectId,
      tool_name: toolName,
      tool_call_id: toolCallId,
      duration_ms: Date.now() - startedAt,
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}

function createTools(request) {
  const { project_id: projectId, backend_api_base_url: baseUrl, internal_tool_token: token } = request;
  return [
    {
      name: "get_project_context",
      label: "Read project context",
      description: "Read the current Blueprint project. The blueprint is represented by cards. Returns cards, modules, materialized assets, runs, and claims. Use when current ids or state matter.",
      parameters: Type.Object({}),
      execute: async (toolCallId, _params, signal) => {
        const payload = await callLoggedTool(
          "get_project_context",
          toolCallId,
          projectId,
          baseUrl,
          token,
          `/internal/manager-tools/projects/${projectId}/context`,
          {},
          signal,
        );
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
    },
    {
      name: "list_data_assets",
      label: "Read data assets timeline",
      description: "Read materialized assets, planned card outputs, card timeline, producer/consumer relations, and workspace path mapping. Use when choosing input asset_ids, output asset_ids, file ids, or card step layers.",
      parameters: Type.Object({}),
      execute: async (toolCallId, _params, signal) => {
        const payload = await callLoggedTool(
          "list_data_assets",
          toolCallId,
          projectId,
          baseUrl,
          token,
          `/internal/manager-tools/projects/${projectId}/data-assets`,
          {},
          signal,
        );
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
    },
    {
      name: "create_card",
      label: "Create card",
      description: "Create a new blueprint card directly. A card is a blueprint unit. Use inputs[].asset_id and outputs[].asset_id to connect the DAG; use step to place it in the timeline. Backend validation returns ok:false with retry hints when arguments need correction.",
      parameters: Type.Object({
        card_id: Type.String(),
        card_type: Type.String({ description: "Usually module or module_group." }),
        title: Type.String(),
        status: Type.String({ description: "Usually planned for future work; proposed for tentative work; accepted only for completed accepted work; cancelled for removed work." }),
        step: Type.Optional(Type.Number()),
        summary: Type.String(),
        why: Type.Optional(Type.String()),
        inputs: Type.Optional(Type.Array(Type.Record(Type.String(), Type.Any()), { description: "Array of { label, asset_id?, status? }. Use exact asset_id values from list_data_assets when known." })),
        outputs: Type.Optional(Type.Array(Type.Record(Type.String(), Type.Any()), { description: "Array of { label, asset_id?, status? }. These are expected/planned assets for downstream cards." })),
        key_findings: Type.Optional(Type.Array(Type.String())),
        manager_review: Type.Optional(Type.String()),
        next_actions: Type.Optional(Type.Array(Type.String())),
        linked_modules: Type.Optional(Type.Array(Type.String())),
        linked_runs: Type.Optional(Type.Array(Type.String())),
        linked_assets: Type.Optional(Type.Array(Type.String())),
        progress_note: Type.Optional(Type.String()),
        executor_context: Type.Optional(Type.Record(Type.String(), Type.Any())),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "create_card",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/cards`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify({ ok: true, ...payload }, null, 2), { ok: true, ...payload });
        } catch (error) {
          return toolErrorResult(error, { error_type: "create_card_failed", tool_name: "create_card" });
        }
      },
    },
    {
      name: "update_card",
      label: "Update card",
      description: "Update an existing blueprint card directly. Use this for modifying blueprint content, status, step, inputs, outputs, or linked assets. Backend validation returns ok:false with retry hints when arguments need correction.",
      parameters: Type.Object({
        card_id: Type.String(),
        card_type: Type.Optional(Type.String({ description: "Usually module or module_group." })),
        title: Type.Optional(Type.String()),
        status: Type.Optional(Type.String({ description: "Usually planned, proposed, accepted, cancelled, failed, stale, or superseded." })),
        step: Type.Optional(Type.Number()),
        summary: Type.Optional(Type.String()),
        why: Type.Optional(Type.String()),
        inputs: Type.Optional(Type.Array(Type.Record(Type.String(), Type.Any()))),
        outputs: Type.Optional(Type.Array(Type.Record(Type.String(), Type.Any()))),
        key_findings: Type.Optional(Type.Array(Type.String())),
        manager_review: Type.Optional(Type.String()),
        next_actions: Type.Optional(Type.Array(Type.String())),
        linked_modules: Type.Optional(Type.Array(Type.String())),
        linked_runs: Type.Optional(Type.Array(Type.String())),
        linked_assets: Type.Optional(Type.Array(Type.String())),
        progress_note: Type.Optional(Type.String()),
        executor_context: Type.Optional(Type.Record(Type.String(), Type.Any())),
      }),
      execute: async (toolCallId, params, signal) => {
        const { card_id: cardId, ...body } = params;
        try {
          const payload = await callLoggedTool(
            "update_card",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/cards/${cardId}`,
            {
              method: "PATCH",
              body,
            },
            signal,
          );
          return textResult(JSON.stringify({ ok: true, ...payload }, null, 2), { ok: true, ...payload });
        } catch (error) {
          return toolErrorResult(error, { error_type: "update_card_failed", tool_name: "update_card" });
        }
      },
    },
    {
      name: "delete_card",
      label: "Delete card",
      description: "Cancel a blueprint card by exact card_id. This marks the card as cancelled instead of deleting historical records. Use get_project_context first if the exact card_id is uncertain.",
      parameters: Type.Object({
        card_id: Type.String(),
        reason: Type.Optional(Type.String()),
        message: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        const { card_id: cardId, ...body } = params;
        try {
          const payload = await callLoggedTool(
            "delete_card",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/cards/${cardId}`,
            {
              method: "DELETE",
              body,
            },
            signal,
          );
          return textResult(JSON.stringify({ ok: true, ...payload }, null, 2), { ok: true, ...payload });
        } catch (error) {
          return toolErrorResult(error, { error_type: "delete_card_failed", tool_name: "delete_card" });
        }
      },
    },
    {
      name: "read_result_asset",
      label: "Read result asset",
      description: "Read a whitelisted result asset detail/preview by exact asset_id.",
      parameters: Type.Object({
        asset_id: Type.String(),
      }),
      execute: async (toolCallId, params, signal) => {
        const payload = await callLoggedTool(
          "read_result_asset",
          toolCallId,
          projectId,
          baseUrl,
          token,
          `/internal/manager-tools/projects/${projectId}/assets/${params.asset_id}`,
          {},
          signal,
        );
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

function createRunId() {
  return `mgr_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
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
  const runId = createRunId();
  const runStartedAt = Date.now();
  const projectId = payload.project_id;
  if (!API_KEY) {
    throw new Error("MANAGER_AGENT_API_KEY or BLUEPRINT_DEEPSEEK_API_KEY is not configured.");
  }
  const model = getModel(PROVIDER, MODEL);
  if (!model) {
    throw new Error(`Manager model not found: provider=${PROVIDER}, model=${MODEL}`);
  }
  const events = [];
  let finalAssistantMessage = null;
  let streamedText = "";
  const thinkingBlocks = new Map();
  let lastEmitAt = Date.now();
  let lastAgentEventAt = Date.now();
  let lastAgentEventType = "run_start";
  let syntheticThinkingOpen = false;
  logManagerEvent("run_start", {
    run_id: runId,
    project_id: projectId,
    model: MODEL,
    provider: PROVIDER,
    thinking_effort: payload.thinking_effort,
    message_chars: typeof payload.message === "string" ? payload.message.length : null,
    history_messages: Array.isArray(payload.messages) ? payload.messages.length : 0,
  });
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
    lastAgentEventAt = Date.now();
    lastAgentEventType = event.type;
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
      logManagerEvent("tool_execution_start", {
        run_id: runId,
        project_id: projectId,
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
      });
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
      const toolFailed = Boolean(event.isError || details?.ok === false);
      logManagerEvent("tool_execution_end", {
        run_id: runId,
        project_id: projectId,
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
        is_error: toolFailed,
        details_bytes: payloadSize(details),
        ...summarizeToolPayload(event.toolName, details),
      });
      emit({
        type: "tool_end",
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
        label: toolFailed ? `${label.active}失败` : label.done,
        done_label: label.done,
        is_error: toolFailed,
      });
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      finalAssistantMessage = event.message;
    }
  });
  const userEnvelope = {
    user_request: payload.message,
    selected_context: payload.context || {},
    script_preference_guidance: scriptPreferenceGuidance(payload.context?.script_preference),
    instruction:
      "Answer naturally. Decide whether project tools are needed. If you change the blueprint, remember that cards are the blueprint units and use create_card, update_card, or delete_card directly. After ok:false tool results, correct and retry when the fix is clear.",
  };
  const abortController = new AbortController();
  const timeoutId = setTimeout(() => {
    logManagerEvent("run_timeout", {
      run_id: runId,
      project_id: projectId,
      elapsed_ms: Date.now() - runStartedAt,
      last_agent_event_type: lastAgentEventType,
      idle_ms: Date.now() - lastAgentEventAt,
    });
    abortController.abort(new Error("manager_timeout"));
  }, TIMEOUT_MS);
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
  const waitLogId = setInterval(() => {
    logManagerEvent("run_waiting", {
      run_id: runId,
      project_id: projectId,
      elapsed_ms: Date.now() - runStartedAt,
      last_agent_event_type: lastAgentEventType,
      idle_ms: Date.now() - lastAgentEventAt,
      streamed_chars: streamedText.length,
      event_count: events.length,
    });
  }, WAIT_LOG_INTERVAL_MS);
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
    clearInterval(waitLogId);
    if (heartbeatId) {
      clearInterval(heartbeatId);
    }
  }
  const thinking = collectThinking(thinkingBlocks) || undefined;
  const text = streamedText.trim() || extractText(finalAssistantMessage).trim();
  const stopReason = finalAssistantMessage?.stopReason;
  if (stopReason === "error" || stopReason === "aborted") {
    logManagerEvent("run_error", {
      run_id: runId,
      project_id: projectId,
      elapsed_ms: Date.now() - runStartedAt,
      stop_reason: stopReason,
      error: finalAssistantMessage.errorMessage || `Manager agent stopped with ${stopReason}`,
      event_count: events.length,
    });
    throw new Error(finalAssistantMessage.errorMessage || `Manager agent stopped with ${stopReason}`);
  }
  if (!text) {
    logManagerEvent("run_error", {
      run_id: runId,
      project_id: projectId,
      elapsed_ms: Date.now() - runStartedAt,
      error: "Manager agent returned an empty response.",
      event_count: events.length,
    });
    throw new Error("Manager agent returned an empty response.");
  }
  logManagerEvent("run_done", {
    run_id: runId,
    project_id: projectId,
    elapsed_ms: Date.now() - runStartedAt,
    outcome: "text",
    event_count: events.length,
    streamed_chars: streamedText.length,
  });
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
