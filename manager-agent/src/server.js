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

You are an interactive chat agent. Answer ordinary questions directly. Use tools only when current project context, data asset timeline, result asset preview, or card changes are needed.

Hard permissions:
- You may read project context through get_project_context.
- You may inspect data assets, planned outputs, producer/consumer relations, and workspace paths through list_data_assets.
- You may create, update, and cancel cards through create_card, update_card, and delete_card.
- You may read result assets through read_result_asset, but not arbitrary files.
- You must not run shell commands, write scripts, execute analyses, or edit files.
- If a tool fails validation, inspect the error, correct your arguments, and retry when possible.

Tool-use contract:
- For greetings, explanations, brainstorming, or analysis suggestions, answer in text without tools unless current project state is required.
- Before changing a card, call get_project_context if exact ids are uncertain.
- Call list_data_assets when selecting input asset ids, file ids, step numbers, or downstream dependencies.
- Do not use or mention blueprint proposal, blueprint review, or approval flows. Card tools apply direct validated changes.
- For multi-step workflow creation, create the next valid card layer. Use card.outputs[].asset_id as the source of planned assets and downstream card.inputs[].asset_id to reuse those ids.
- If the backend says a card step is too early, increase step to the required value. If an input asset is missing, use list_data_assets or create an upstream card output first.
- Reuse existing card ids and module ids when updating existing work. Create new ids only for genuinely new cards.
- Keep final replies concise and user-facing.

Card fields:
- Required for create_card: card_id, card_type, title, status, summary.
- Useful fields: step, why, inputs, outputs, key_findings, manager_review, next_actions, linked_modules, linked_runs, linked_assets, progress_note.
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
      description: "Read the current Blueprint project, cards, modules, assets, runs, and claims. This is read-only. Use before referencing ids or changing cards.",
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
      description: "Read data assets, planned outputs, card timeline, and workspace path mapping. Use this when you need step order, producer/consumer relations, or to resolve file ids to workspace paths.",
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
      description: "Create a card directly. Use step, inputs, outputs, and linked_modules to express the card's place in the timeline.",
      parameters: Type.Object({
        card_id: Type.String(),
        card_type: Type.String(),
        title: Type.String(),
        status: Type.String(),
        step: Type.Optional(Type.Number()),
        summary: Type.String(),
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
      }),
      execute: async (toolCallId, params, signal) => {
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
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
    },
    {
      name: "update_card",
      label: "Update card",
      description: "Update an existing card directly. The backend will reject cards whose step is earlier than their input assets require.",
      parameters: Type.Object({
        card_id: Type.String(),
        card_type: Type.Optional(Type.String()),
        title: Type.Optional(Type.String()),
        status: Type.Optional(Type.String()),
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
      }),
      execute: async (toolCallId, params, signal) => {
        const { card_id: cardId, ...body } = params;
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
        return textResult(JSON.stringify(payload, null, 2), payload, true);
      },
    },
    {
      name: "delete_card",
      label: "Delete card",
      description: "Cancel a card by exact card_id.",
      parameters: Type.Object({
        card_id: Type.String(),
        reason: Type.Optional(Type.String()),
        message: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        const { card_id: cardId, ...body } = params;
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
        return textResult(JSON.stringify(payload, null, 2), payload, true);
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
      logManagerEvent("tool_execution_end", {
        run_id: runId,
        project_id: projectId,
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
        is_error: Boolean(event.isError),
        details_bytes: payloadSize(details),
        ...summarizeToolPayload(event.toolName, details),
      });
      emit({
        type: "tool_end",
        tool_name: event.toolName,
        tool_call_id: event.toolCallId,
        label: event.isError ? `${label.active}失败` : label.done,
        done_label: label.done,
        is_error: Boolean(event.isError),
      });
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      finalAssistantMessage = event.message;
    }
  });
  const userEnvelope = {
    user_request: payload.message,
    selected_context: payload.context || {},
    instruction:
      "Answer naturally. Use tools only when the user asks about current project state, data asset timeline, result assets, or card changes. For card changes, choose the next valid card layer from the timeline and use create_card, update_card, or delete_card directly.",
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
