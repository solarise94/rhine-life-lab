import {
  Agent,
  DEFAULT_COMPACTION_SETTINGS,
  compact,
  estimateContextTokens,
  prepareCompaction,
} from "@earendil-works/pi-agent-core";
import { clampThinkingLevel, getModel, registerBuiltInApiProviders, Type } from "@earendil-works/pi-ai";
import { buildSessionContext } from "../node_modules/@earendil-works/pi-agent-core/dist/harness/session/session.js";

registerBuiltInApiProviders();

const HOST = process.env.MANAGER_AGENT_HOST || "127.0.0.1";
const PORT = Number(process.env.MANAGER_AGENT_PORT || "18002");
const PROVIDER = process.env.MANAGER_AGENT_PROVIDER || "deepseek";
const MODEL = process.env.MANAGER_AGENT_MODEL || process.env.BLUEPRINT_MANAGER_MODEL || "deepseek-v4-pro";
const API_KEY = process.env.MANAGER_AGENT_API_KEY || process.env.BLUEPRINT_DEEPSEEK_API_KEY || "";
const TIMEOUT_MS = Number(process.env.MANAGER_AGENT_TIMEOUT_MS || "600000");
const HEARTBEAT_INTERVAL_MS = 5000;
const WAIT_LOG_INTERVAL_MS = Number(process.env.MANAGER_AGENT_WAIT_LOG_INTERVAL_MS || "30000");
const MANAGER_WEBSEARCH_ENABLED = /^(1|true|yes|on)$/i.test(process.env.MANAGER_WEBSEARCH_ENABLED || "");
const TAVILY_API_KEY = process.env.TAVILY_API_KEY || "";
const TAVILY_BASE_URL = process.env.TAVILY_BASE_URL || "https://api.tavily.com";
const MANAGER_CONTEXT_WINDOW_TOKENS = Number(process.env.MANAGER_CONTEXT_WINDOW_TOKENS || "1000000");
const MANAGER_COMPACTION_ENABLED = !/^(0|false|no|off)$/i.test(process.env.MANAGER_COMPACTION_ENABLED || "true");
const MANAGER_COMPACTION_KEEP_RECENT_TOKENS = Number(process.env.MANAGER_COMPACTION_KEEP_RECENT_TOKENS || "120000");
const MANAGER_COMPACTION_RESERVE_TOKENS = Number(process.env.MANAGER_COMPACTION_RESERVE_TOKENS || "16000");

function resolveManagerConfig(payload = {}) {
  const config = payload.manager_config && typeof payload.manager_config === "object" ? payload.manager_config : {};
  return {
    provider: config.provider || PROVIDER,
    model: config.model || MODEL,
    apiKey: config.api_key || API_KEY,
    deepseekApiBaseUrl: config.deepseek_api_base_url || process.env.BLUEPRINT_DEEPSEEK_API_BASE_URL || "",
    piDeepseekBaseUrl: config.pi_deepseek_base_url || process.env.BLUEPRINT_PI_DEEPSEEK_BASE_URL || "",
    websearchEnabled:
      typeof config.websearch_enabled === "boolean" ? config.websearch_enabled : MANAGER_WEBSEARCH_ENABLED,
    tavilyApiKey: config.tavily_api_key || TAVILY_API_KEY,
    tavilyBaseUrl: config.tavily_base_url || TAVILY_BASE_URL,
  };
}

function normalizeBaseUrl(value) {
  return typeof value === "string" ? value.trim().replace(/\/+$/, "") : "";
}

function resolveModel(runtimeConfig) {
  const model = getModel(runtimeConfig.provider, runtimeConfig.model);
  if (!model) {
    return null;
  }
  const deepseekBaseUrl = normalizeBaseUrl(runtimeConfig.piDeepseekBaseUrl);
  if (runtimeConfig.provider === "deepseek" && deepseekBaseUrl) {
    return { ...model, baseUrl: deepseekBaseUrl };
  }
  return model;
}

function buildSystemPrompt(runtimeConfig = resolveManagerConfig()) {
  const webCapabilityLines =
    runtimeConfig.websearchEnabled && runtimeConfig.tavilyApiKey
      ? [
          "- web_search finds current public web information when up-to-date external context is required.",
          "- web_extract reads the content of a specific public web page after search identifies a source.",
        ]
      : [];
  const webJudgmentLines =
    runtimeConfig.websearchEnabled && runtimeConfig.tavilyApiKey
      ? [
          "- Use web_search or web_extract when the user explicitly asks for current/latest information, when external docs or recent package behavior matters, or when you need to verify a claim before editing the blueprint.",
          "- Do not use web tools when project context is already sufficient, or when doing so could expose local secrets or private project content.",
        ]
      : [];
  return `You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

You are an interactive project agent. Answer directly when the user is asking a general question. Use tools when they materially improve correctness or when the user asks you to inspect or change the current project.

Core model:
- The blueprint is represented by cards. A card is the editable unit of the blueprint.
- Data assets are referenced by asset_id. Card outputs are expected/planned assets; later card inputs can reuse those asset_ids.
- Card step is the timeline layer. A card must be later than the assets it consumes.

Available capabilities:
- get_project_context reads current cards, modules, assets, runs, and claims.
- list_data_assets reads materialized assets, planned outputs, workspace paths, producer/consumer relations, and timeline steps.
- list_project_memory reads short-lived-to-long-term project preferences and corrections. It is not the source of project execution facts.
- write_project_memory stores only explicit user preferences and corrections, such as "remember this", "default to this", or "do not do this again".
- create_card, update_card, and delete_card directly modify blueprint cards after backend validation.
- configure_card_execution directly updates card execution permissions/runtime bindings such as tool_policy.rscript and tool_policy.network.
- install_runtime_dependencies starts a background job that installs explicitly named Python/R packages into an already selected non-system runtime when a card reports missing runtime dependencies.
- get_runtime_dependency_install_status checks whether a previously started dependency installation job has finished.
- start_card_run, stop_card_run, rerun_card, and review_card_run control card execution directly when execution should happen now.
- cleanup_run_history removes old finished run execution files/caches when they are no longer needed; by default it preserves runs that own valid accepted assets.
- search_card_templates, save_card_template, and instantiate_card_template manage reusable manager-only card templates.
- read_result_asset reads a whitelisted result asset preview by asset_id.
${webCapabilityLines.join("\n")}

Judgment:
- Decide whether current project context is needed. If exact card ids, asset ids, steps, or current blueprint state matter, inspect the project first.
- For broad workflow additions, inspect data assets/timeline before choosing steps and asset_ids.
- For plotting style, report style, recurring user preferences, or previously corrected behavior, read project memory when relevant.
- Treat the blueprint/cards/assets/runs as the source of project execution facts. Do not write blueprint facts into project memory.
- For simple conceptual questions, answer without tools.
- For blueprint/card changes, use card tools directly once you have enough context. Do not describe a change as complete unless a write tool succeeded.
- If a write tool returns ok:false, use the message/retry_hint to correct arguments and retry when the correction is clear. If it is not clear, inspect context or ask a focused question.
${webJudgmentLines.join("\n")}
- Write project memory only when the user explicitly asks you to remember a durable preference, says a behavior should be the default, or corrects something you should avoid in future. Keep memory summaries short.
- Do not ask the user to approve executor runtime permissions in a card prompt. Card agents cannot ask the user interactively. If a card needs Rscript or network access, use configure_card_execution on that card before telling the user it is ready.
- Card executor agents run in a constrained runtime. They must not install missing R or Python packages on their own. If runtime packages are missing and a specific non-system runtime is selected, you may use install_runtime_dependencies with explicit package names to start a background install job, then check it with get_runtime_dependency_install_status when needed. If that fails or the missing dependency is a system tool, tell the user exactly what dependency must be prepared.
- If a task looks like a stable repeated workflow, search_card_templates before creating a new analysis card from scratch.
- When a template requires script assets, ask the user which project script assets to bind before instantiate_card_template or before starting the card. Do not make card agents ask the user for bindings.
- For multi-step workflow creation, you may create multiple cards in one conversation. Re-check the timeline when useful.
- Reuse existing card ids when updating existing work. Create new ids only for genuinely new cards.
- Do not use or mention blueprint proposal, blueprint review, or approval flows. Card tools are the source of truth for blueprint edits.
- Respect selected_context.script_preference when creating analysis cards. It is a soft script-language preference, not a hard constraint.
- Respect selected_context.python_runtime and selected_context.r_runtime as preferred execution runtimes when planning or updating analysis cards.
- If script_preference is auto and a new bioinformatics card could reasonably be implemented in either Python or R, ask the user which script style they prefer when that choice materially affects the workflow.
- When a concrete script preference is known, add it to executor_context.instruction_blocks on new or updated analysis cards.
- Keep final replies concise and user-facing.

Card fields:
- Required for create_card: card_id, card_type, title, status, summary.
- Common card_type values: module, module_group.
- Common status values: planned, proposed, accepted, cancelled, failed, stale, superseded.
- Useful fields: step, why, inputs, outputs, key_findings, manager_review, next_actions, linked_modules, linked_runs, linked_assets, progress_note.
- executor_context may include instruction_blocks for soft execution guidance such as script-language preference. Prefer configure_card_execution for tool_policy/runtime permission changes.
- Inputs and outputs are arrays shaped like { label, asset_id?, status? }.
- Prefer status "planned" for future work, "cancelled" for dormant/deleted cards, and "accepted" only for completed accepted work.`;
}

const TOOL_STATUS_LABELS = {
  get_project_context: {
    active: "正在查看蓝图",
    done: "已查看蓝图",
  },
  list_data_assets: {
    active: "正在查看数据资产",
    done: "已查看数据资产",
  },
  list_project_memory: {
    active: "正在读取项目记忆",
    done: "已读取项目记忆",
  },
  write_project_memory: {
    active: "正在写入项目记忆",
    done: "已写入项目记忆",
  },
  create_card: {
    active: "正在创建卡片",
    done: "已创建卡片",
  },
  update_card: {
    active: "正在更新卡片",
    done: "已更新卡片",
  },
  configure_card_execution: {
    active: "正在配置卡片权限",
    done: "已配置卡片权限",
  },
  install_runtime_dependencies: {
    active: "正在提交环境依赖任务",
    done: "已提交环境依赖任务",
  },
  get_runtime_dependency_install_status: {
    active: "正在检查环境依赖任务",
    done: "已检查环境依赖任务",
  },
  start_card_run: {
    active: "正在启动卡片",
    done: "已启动卡片",
  },
  stop_card_run: {
    active: "正在停止运行",
    done: "已停止运行",
  },
  rerun_card: {
    active: "正在重跑卡片",
    done: "已重跑卡片",
  },
  review_card_run: {
    active: "正在审核运行",
    done: "已审核运行",
  },
  cleanup_run_history: {
    active: "正在清理运行历史",
    done: "已清理运行历史",
  },
  delete_card: {
    active: "正在删除卡片",
    done: "已删除卡片",
  },
  read_result_asset: {
    active: "正在读取结果文件",
    done: "已读取结果文件",
  },
  search_card_templates: {
    active: "正在查询模板",
    done: "已查询模板",
  },
  save_card_template: {
    active: "正在保存模板",
    done: "已保存模板",
  },
  instantiate_card_template: {
    active: "正在实例化模板",
    done: "已实例化模板",
  },
  web_search: {
    active: "正在搜索网页",
    done: "已搜索网页",
  },
  web_extract: {
    active: "正在读取网页",
    done: "已读取网页",
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

function runtimePreferenceGuidance(context = {}) {
  const pythonRuntime = context?.python_runtime || null;
  const rRuntime = context?.r_runtime || null;
  const instructions = [];
  if (pythonRuntime) {
    instructions.push(`Preferred Python runtime for future card execution: ${pythonRuntime}.`);
  }
  if (rRuntime) {
    instructions.push(`Preferred R runtime for future card execution: ${rRuntime}.`);
  }
  if (!instructions.length) {
    return { python_runtime: null, r_runtime: null, card_instruction_block: null };
  }
  return {
    python_runtime: pythonRuntime,
    r_runtime: rRuntime,
    card_instruction_block: `Runtime preference: ${instructions.join(" ")} Add this to executor_context.instruction_blocks when it is relevant to a new or updated analysis card.`,
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
  if (toolName === "list_project_memory") {
    return {
      memory_items: Array.isArray(payload.items) ? payload.items.length : undefined,
    };
  }
  if (toolName === "write_project_memory") {
    return {
      memory_id: payload.memory?.memory_id,
      memory_kind: payload.memory?.kind,
      items_count: payload.items_count,
    };
  }
  if (toolName === "install_runtime_dependencies" || toolName === "get_runtime_dependency_install_status") {
    return {
      job_id: payload.job_id,
      status: payload.status,
      runtime: payload.runtime,
      packages: Array.isArray(payload.packages) ? payload.packages.length : undefined,
      background: payload.background,
      ok: payload.ok,
    };
  }
  if (toolName === "start_card_run" || toolName === "rerun_card") {
    return {
      run_id: payload.run_id,
      card_id: payload.card_id,
      status: payload.status,
      can_start: payload.can_start,
    };
  }
  if (toolName === "stop_card_run" || toolName === "review_card_run") {
    return {
      run_id: payload.run_id,
      status: payload.status,
      accepted: payload.accepted,
      stopped: payload.stopped,
    };
  }
  if (toolName === "cleanup_run_history") {
    return {
      cleaned_count: payload.cleaned_count,
      skipped_count: payload.skipped_count,
      dry_run: payload.dry_run,
      ok: payload.ok,
    };
  }
  if (toolName === "search_card_templates") {
    return {
      templates: Array.isArray(payload.items) ? payload.items.length : undefined,
      total: payload.total,
    };
  }
  if (toolName === "save_card_template") {
    return {
      template_id: payload.template?.template_id,
      card_type: payload.template?.card_type,
    };
  }
  if (toolName === "instantiate_card_template") {
    return {
      card_id: payload.card?.card_id,
      card_status: payload.card?.status,
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

function buildToolReport(toolName, details) {
  if (!details || typeof details !== "object") {
    return null;
  }
  if (toolName === "install_runtime_dependencies" && details.background && details.job_id) {
    const runtime = details.runtime || "selected runtime";
    const packageCount = Array.isArray(details.packages) ? details.packages.length : 0;
    return {
      summary:
        details.message ||
        `已启动后台依赖安装任务：${runtime}${packageCount ? `，共 ${packageCount} 个包` : ""}。`,
      details,
    };
  }
  if (toolName === "get_runtime_dependency_install_status" && details.job_id) {
    const status = details.status || "unknown";
    return {
      summary:
        details.message ||
        (status === "succeeded"
          ? "后台依赖安装已完成。"
          : status === "failed"
            ? "后台依赖安装失败。"
            : "后台依赖安装仍在进行。"),
      details,
    };
  }
  return null;
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

function createTools(request, runtimeConfig = resolveManagerConfig(request)) {
  const { project_id: projectId, backend_api_base_url: baseUrl, internal_tool_token: token } = request;
  const tools = [
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
      name: "list_project_memory",
      label: "Read project memory",
      description: "Read short project memory containing only explicit user preferences and corrections. Use for plotting style, report style, recurring preferences, or avoiding previously corrected behavior. Do not use it as the source of project execution facts; use blueprint tools for that.",
      parameters: Type.Object({
        kind: Type.Optional(Type.String({ description: "user_preference or correction_memory" })),
        query: Type.Optional(Type.String()),
        limit: Type.Optional(Type.Number({ description: "Maximum memories to return. Defaults to 5, capped by backend." })),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "list_project_memory",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/memory/list`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "list_project_memory_failed", tool_name: "list_project_memory" });
        }
      },
    },
    {
      name: "write_project_memory",
      label: "Write project memory",
      description: "Store or update a durable project memory. Only write when the user explicitly asks you to remember a preference/default or corrects behavior to avoid. Valid kinds are user_preference and correction_memory. Keep summaries short and do not store blueprint facts.",
      parameters: Type.Object({
        memory_id: Type.Optional(Type.String()),
        kind: Type.String({ description: "user_preference or correction_memory" }),
        summary: Type.String({ description: "Short durable rule. Do not include raw chat transcripts or project execution facts." }),
        source: Type.Optional(Type.String()),
        confidence: Type.Optional(Type.Number()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "write_project_memory",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/memory`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify({ ok: true, ...payload }, null, 2), { ok: true, ...payload });
        } catch (error) {
          return toolErrorResult(error, { error_type: "write_project_memory_failed", tool_name: "write_project_memory" });
        }
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
      name: "configure_card_execution",
      label: "Configure card execution",
      description: "Update execution permissions and runtime bindings for one or more cards. Use this when cards need Rscript, network access, selected runtimes, or non-interactive permission policy changes. This merges into existing executor_context without rewriting the whole card.",
      parameters: Type.Object({
        card_id: Type.Optional(Type.String()),
        card_ids: Type.Optional(Type.Array(Type.String())),
        tool_policy: Type.Optional(
          Type.Object({
            network: Type.Optional(Type.String({ description: "allow, deny, or prompt. Use allow when the card agent must access model APIs or download databases without asking the user." })),
            python: Type.Optional(Type.Boolean()),
            rscript: Type.Optional(Type.Boolean({ description: "Set true for R/GSVA/ESTIMATE-style cards." })),
            shell: Type.Optional(Type.Boolean()),
            git_write: Type.Optional(Type.Boolean()),
          }),
        ),
        runtime_bindings: Type.Optional(
          Type.Object({
            conda_env: Type.Optional(Type.String()),
            r_env: Type.Optional(Type.String()),
            working_dir: Type.Optional(Type.String()),
            env: Type.Optional(Type.Record(Type.String(), Type.String())),
          }),
        ),
        instruction_blocks: Type.Optional(Type.Array(Type.String())),
        progress_note: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "configure_card_execution",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/card-execution`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify({ ok: true, ...payload }, null, 2), { ok: true, ...payload });
        } catch (error) {
          return toolErrorResult(error, { error_type: "configure_card_execution_failed", tool_name: "configure_card_execution" });
        }
      },
    },
    {
      name: "install_runtime_dependencies",
      label: "Install runtime dependencies",
      description: "Start a background job that installs explicitly named Python or R packages into an already selected non-system runtime after a card reports missing runtime dependencies. Use only for clear package lists.",
      parameters: Type.Object({
        ecosystem: Type.String({ description: "python or R" }),
        runtime: Type.String({ description: "Selected non-system runtime name, such as omicverse, rnaseq, or R_env. Do not use __system__." }),
        packages: Type.Array(Type.String({ description: "Package names or simple Python version specs." })),
        manager: Type.Optional(Type.String({ description: "For python: pip or conda. For R: bioconductor or cran. Defaults to pip for python and bioconductor for R." })),
        timeout_seconds: Type.Optional(Type.Number()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "install_runtime_dependencies",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runtime-dependencies/install`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "install_runtime_dependencies_failed", tool_name: "install_runtime_dependencies" });
        }
      },
    },
    {
      name: "get_runtime_dependency_install_status",
      label: "Get runtime dependency install status",
      description: "Check whether a background runtime dependency installation job has finished, failed, or is still running.",
      parameters: Type.Object({
        job_id: Type.String(),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "get_runtime_dependency_install_status",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runtime-dependencies/jobs/${encodeURIComponent(params.job_id)}`,
            {
              method: "GET",
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, {
            error_type: "get_runtime_dependency_install_status_failed",
            tool_name: "get_runtime_dependency_install_status",
          });
        }
      },
    },
    {
      name: "start_card_run",
      label: "Start card run",
      description: "Start executing a specific card. Use after the card plan and runtime policy are ready. If can_start is false, inspect block_reasons and fix the blocker before retrying.",
      parameters: Type.Object({
        card_id: Type.String(),
        worker_type: Type.Optional(Type.String()),
        python_runtime: Type.Optional(Type.String()),
        r_runtime: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "start_card_run",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runs/start`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "start_card_run_failed", tool_name: "start_card_run" });
        }
      },
    },
    {
      name: "stop_card_run",
      label: "Stop card run",
      description: "Stop an active run by run_id, or by card_id if the card currently has one active run.",
      parameters: Type.Object({
        run_id: Type.Optional(Type.String()),
        card_id: Type.Optional(Type.String()),
        reason: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "stop_card_run",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runs/stop`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "stop_card_run_failed", tool_name: "stop_card_run" });
        }
      },
    },
    {
      name: "rerun_card",
      label: "Rerun card",
      description: "Start a fresh rerun for a card after a previous run finished or failed.",
      parameters: Type.Object({
        card_id: Type.String(),
        worker_type: Type.Optional(Type.String()),
        python_runtime: Type.Optional(Type.String()),
        r_runtime: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "rerun_card",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runs/rerun`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "rerun_card_failed", tool_name: "rerun_card" });
        }
      },
    },
    {
      name: "review_card_run",
      label: "Review card run",
      description: "Accept or reject the latest run for a card, or a specific run_id when you need to finalize a reviewed result.",
      parameters: Type.Object({
        run_id: Type.Optional(Type.String()),
        card_id: Type.Optional(Type.String()),
        accept: Type.Optional(Type.Boolean()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "review_card_run",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runs/review`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "review_card_run_failed", tool_name: "review_card_run" });
        }
      },
    },
    {
      name: "cleanup_run_history",
      label: "Clean run history",
      description: "Remove old finished run execution files, caches, transient state, and candidate artifacts. Use after failed/cancelled/rejected runs accumulate or before reruns. Defaults preserve the latest run per card and runs that own valid accepted assets.",
      parameters: Type.Object({
        run_id: Type.Optional(Type.String({ description: "Clean one specific run. If omitted, cleanup can target card_id or project-wide old runs." })),
        card_id: Type.Optional(Type.String({ description: "Clean old runs for one card." })),
        statuses: Type.Optional(Type.Array(Type.String({ description: "Run statuses to consider. Defaults to failed, cancelled, reviewed." }))),
        keep_latest_per_card: Type.Optional(Type.Boolean({ description: "Default true. Keep the newest run for each card unless run_id is specified." })),
        include_valid_assets: Type.Optional(Type.Boolean({ description: "Default false. Leave runs that own valid accepted assets untouched." })),
        dry_run: Type.Optional(Type.Boolean({ description: "Preview what would be cleaned without deleting files." })),
        reason: Type.Optional(Type.String()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "cleanup_run_history",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/runs/cleanup-history`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "cleanup_run_history_failed", tool_name: "cleanup_run_history" });
        }
      },
    },
    {
      name: "search_card_templates",
      label: "Search card templates",
      description: "Search the manager-only local card template library before designing a repeated workflow from scratch.",
      parameters: Type.Object({
        query: Type.Optional(Type.String()),
        tags: Type.Optional(Type.Array(Type.String())),
        card_type: Type.Optional(Type.String()),
        limit: Type.Optional(Type.Number()),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "search_card_templates",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/card-templates/search`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "search_card_templates_failed", tool_name: "search_card_templates" });
        }
      },
    },
    {
      name: "save_card_template",
      label: "Save card template",
      description: "Save a stable accepted/reviewer-passed card into the manager-only card template library for later reuse.",
      parameters: Type.Object({
        card_id: Type.String(),
        title: Type.Optional(Type.String()),
        summary: Type.Optional(Type.String()),
        tags: Type.Optional(Type.Array(Type.String())),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "save_card_template",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/card-templates`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        } catch (error) {
          return toolErrorResult(error, { error_type: "save_card_template_failed", tool_name: "save_card_template" });
        }
      },
    },
    {
      name: "instantiate_card_template",
      label: "Instantiate card template",
      description: "Create a new card from a saved template. If the template requires script assets, ask the user which project script assets to bind before calling this tool.",
      parameters: Type.Object({
        template_id: Type.String(),
        card_id: Type.String(),
        title: Type.Optional(Type.String()),
        step: Type.Optional(Type.Number()),
        input_bindings: Type.Optional(Type.Array(Type.Record(Type.String(), Type.Any()))),
        output_bindings: Type.Optional(Type.Array(Type.Record(Type.String(), Type.Any()))),
        script_asset_bindings: Type.Optional(
          Type.Array(
            Type.Object({
              requirement_id: Type.String(),
              asset_id: Type.String(),
            }),
          ),
        ),
        runtime_overrides: Type.Optional(Type.Record(Type.String(), Type.Any())),
      }),
      execute: async (toolCallId, params, signal) => {
        try {
          const payload = await callLoggedTool(
            "instantiate_card_template",
            toolCallId,
            projectId,
            baseUrl,
            token,
            `/internal/manager-tools/projects/${projectId}/card-templates/instantiate`,
            {
              method: "POST",
              body: params,
            },
            signal,
          );
          return textResult(JSON.stringify({ ok: true, ...payload }, null, 2), { ok: true, ...payload });
        } catch (error) {
          return toolErrorResult(error, { error_type: "instantiate_card_template_failed", tool_name: "instantiate_card_template" });
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
  if (runtimeConfig.websearchEnabled && runtimeConfig.tavilyApiKey) {
    tools.push(
      {
        name: "web_search",
        label: "Search the web",
        description: "Search current public web information via Tavily. Use for current docs, recent behavior, or external verification.",
        parameters: Type.Object({
          query: Type.String(),
          search_depth: Type.Optional(Type.String({ description: "basic or advanced" })),
          max_results: Type.Optional(Type.Number()),
          include_domains: Type.Optional(Type.Array(Type.String())),
          exclude_domains: Type.Optional(Type.Array(Type.String())),
        }),
        execute: async (_toolCallId, params, signal) => {
          const payload = await callTavily(
            "/search",
            {
              query: params.query,
              search_depth: params.search_depth || "basic",
              max_results: params.max_results || 5,
              include_domains: params.include_domains,
              exclude_domains: params.exclude_domains,
              include_answer: true,
            },
            signal,
            runtimeConfig,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        },
      },
      {
        name: "web_extract",
        label: "Extract web page content",
        description: "Extract readable markdown or text from specific URLs via Tavily.",
        parameters: Type.Object({
          urls: Type.Array(Type.String()),
          extract_depth: Type.Optional(Type.String({ description: "basic or advanced" })),
          format: Type.Optional(Type.String({ description: "markdown or text" })),
        }),
        execute: async (_toolCallId, params, signal) => {
          const payload = await callTavily(
            "/extract",
            {
              urls: params.urls,
              extract_depth: params.extract_depth || "basic",
              format: params.format || "markdown",
            },
            signal,
            runtimeConfig,
          );
          return textResult(JSON.stringify(payload, null, 2), payload);
        },
      },
    );
  }
  return tools;
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
  return Array.from(thinkingBlocks.values())
    .map((value) => value?.trim())
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

function compactTimelineItem(message) {
  if (!Array.isArray(message?.timeline)) {
    return null;
  }
  return message.timeline.find((item) => item?.kind === "compact" && typeof item?.content === "string" && item.content.trim());
}

const CONTEXT_FALLBACK_MESSAGE_LIMIT = 50;

function messageTextForContext(message) {
  if (!message || typeof message !== "object") {
    return "";
  }
  const compactionItem = compactTimelineItem(message);
  if (compactionItem?.content) {
    return compactionItem.content.trim();
  }
  if (typeof message.content === "string" && message.content.trim()) {
    return message.content.trim();
  }
  return "";
}

function buildSessionEntries(sessionMessages = [], runtimeConfig = resolveManagerConfig()) {
  const entries = [];
  let parentId = null;
  const baseTime = Date.now();
  for (let index = 0; index < sessionMessages.length; index += 1) {
    const message = sessionMessages[index];
    if (!message || (message.role !== "user" && message.role !== "manager")) {
      continue;
    }
    const compactionItem = compactTimelineItem(message);
    const timestamp = new Date(baseTime + index).toISOString();
    if (compactionItem?.content) {
      const entryId = compactionItem.id || message.id || `compact_${index}`;
      entries.push({
        type: "compaction",
        id: entryId,
        parentId,
        timestamp,
        summary: compactionItem.content.trim(),
        firstKeptEntryId: compactionItem.first_kept_message_id || "root",
        tokensBefore: Number(compactionItem.tokens_before) || 0,
        details: {
          tokens_after: Number(compactionItem.tokens_after) || undefined,
          duration_ms: Number(compactionItem.duration_ms) || undefined,
          provider: compactionItem.provider || undefined,
          model: compactionItem.model || undefined,
        },
        fromHook: true,
      });
      parentId = entryId;
      continue;
    }
    const text = messageTextForContext(message);
    if (!text) {
      continue;
    }
    const entryId = message.id || `message_${index}`;
    entries.push({
      type: "message",
      id: entryId,
      parentId,
      timestamp,
      message: {
        role: message.role === "manager" ? "assistant" : "user",
        content: [{ type: "text", text }],
        timestamp: baseTime + index,
        provider: runtimeConfig.provider,
        model: runtimeConfig.model,
      },
    });
    parentId = entryId;
  }
  return entries;
}

function compactionSettings() {
  return {
    ...DEFAULT_COMPACTION_SETTINGS,
    enabled: MANAGER_COMPACTION_ENABLED,
    keepRecentTokens: MANAGER_COMPACTION_KEEP_RECENT_TOKENS,
    reserveTokens: MANAGER_COMPACTION_RESERVE_TOKENS,
  };
}

function currentContextWindow(model) {
  if (MANAGER_CONTEXT_WINDOW_TOKENS > 0) {
    return MANAGER_CONTEXT_WINDOW_TOKENS;
  }
  const fromModel = Number.isFinite(Number(model?.contextWindow)) ? Number(model.contextWindow) : 0;
  return fromModel > 0 ? fromModel : 0;
}

async function maybeCompactSessionHistory({
  sessionMessages,
  model,
  thinkingEffort,
  emitEvent,
  signal,
  auto = true,
  force = false,
  runtimeConfig = resolveManagerConfig(),
}) {
  const entries = buildSessionEntries(sessionMessages, runtimeConfig);
  const context = buildSessionContext(entries);
  const messages = context.messages;
  if (!messages.length) {
    return { compacted: false, contextMessages: messages };
  }
  const settings = compactionSettings();
  if (!settings.enabled) {
    return { compacted: false, contextMessages: messages };
  }
  const estimate = estimateContextTokens(messages);
  const contextWindow = currentContextWindow(model);
  const threshold = contextWindow - settings.reserveTokens;
  if (!force && (estimate.tokens <= 0 || estimate.tokens < threshold)) {
    return { compacted: false, contextMessages: messages };
  }
  const preparationResult = prepareCompaction(entries, settings);
  if (!preparationResult.ok) {
    throw preparationResult.error;
  }
  const preparation = preparationResult.value;
  if (!preparation) {
    return { compacted: false, contextMessages: messages };
  }
  const compactId = `compact_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const startedAt = Date.now();
  emitEvent?.({
    type: "compact_start",
    compact_id: compactId,
    auto,
  });
  const compactResult = await compact(
    preparation,
    model,
    runtimeConfig.apiKey,
    undefined,
    undefined,
    signal,
    mapThinkingLevel(thinkingEffort, model),
  );
  if (!compactResult.ok) {
    emitEvent?.({
      type: "compact_error",
      compact_id: compactId,
      message: compactResult.error.message,
      auto,
    });
    throw compactResult.error;
  }
  const result = compactResult.value;
  const compactionEntry = {
    type: "compaction",
    id: compactId,
    parentId: entries.at(-1)?.id ?? null,
    timestamp: new Date().toISOString(),
    summary: result.summary,
    firstKeptEntryId: result.firstKeptEntryId,
    tokensBefore: result.tokensBefore,
    details: result.details,
    fromHook: auto,
  };
  const compactedMessages = buildSessionContext([...entries, compactionEntry]).messages;
  const tokensAfter = estimateContextTokens(compactedMessages).tokens;
  const durationMs = Date.now() - startedAt;
  emitEvent?.({
    type: "compact_end",
    compact_id: compactId,
    content: result.summary,
    duration_ms: durationMs,
    tokens_before: result.tokensBefore,
    tokens_after: tokensAfter,
    first_kept_message_id: result.firstKeptEntryId,
    provider: runtimeConfig.provider,
    model: runtimeConfig.model,
    auto,
  });
  return {
    compacted: true,
    compactId,
    contextMessages: compactedMessages,
    summary: result.summary,
    firstKeptMessageId: result.firstKeptEntryId,
    tokensBefore: result.tokensBefore,
    tokensAfter,
    durationMs,
    provider: runtimeConfig.provider,
    model: runtimeConfig.model,
  };
}

async function callTavily(path, payload, signal, runtimeConfig = resolveManagerConfig()) {
  if (!runtimeConfig.websearchEnabled || !runtimeConfig.tavilyApiKey) {
    return {
      ok: false,
      disabled: true,
      message: "Web search is disabled. Set MANAGER_WEBSEARCH_ENABLED=true and configure TAVILY_API_KEY.",
    };
  }
  const response = await fetch(`${runtimeConfig.tavilyBaseUrl}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${runtimeConfig.tavilyApiKey}`,
    },
    body: JSON.stringify(payload),
    signal,
  });
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `Tavily request failed with HTTP ${response.status}`);
  }
  return data;
}

function createRunId() {
  return `mgr_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function normalizeTokenUsage(usage, model) {
  if (!usage || typeof usage !== "object") {
    return null;
  }
  const numberOrZero = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
  const inputTokens = numberOrZero(usage.input);
  const outputTokens = numberOrZero(usage.output);
  const cacheReadTokens = numberOrZero(usage.cacheRead);
  const cacheWriteTokens = numberOrZero(usage.cacheWrite);
  const totalTokens =
    numberOrZero(usage.totalTokens) || inputTokens + outputTokens + cacheReadTokens + cacheWriteTokens;
  if (totalTokens <= 0) {
    return null;
  }
  return {
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    cache_read_tokens: cacheReadTokens,
    cache_write_tokens: cacheWriteTokens,
    total_tokens: totalTokens,
    context_window_tokens: currentContextWindow(model) || null,
    max_output_tokens: Number.isFinite(Number(model?.maxTokens)) ? Number(model.maxTokens) : null,
  };
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
  const runtimeConfig = resolveManagerConfig(payload);
  if (!runtimeConfig.apiKey) {
    throw new Error("MANAGER_AGENT_API_KEY or BLUEPRINT_DEEPSEEK_API_KEY is not configured.");
  }
  const model = resolveModel(runtimeConfig);
  if (!model) {
    throw new Error(`Manager model not found: provider=${runtimeConfig.provider}, model=${runtimeConfig.model}`);
  }
  const events = [];
  let finalAssistantMessage = null;
  let finalTokenUsage = null;
  let streamedText = "";
  const thinkingBlocks = new Map();
  const thinkingStartedAt = new Map();
  let assistantTurnIndex = -1;
  let currentAssistantTurnIndex = -1;
  let lastEmitAt = Date.now();
  let lastAgentEventAt = Date.now();
  let lastAgentEventType = "run_start";
  logManagerEvent("run_start", {
    run_id: runId,
    project_id: projectId,
    model: runtimeConfig.model,
    provider: runtimeConfig.provider,
    thinking_effort: payload.thinking_effort,
    message_chars: typeof payload.message === "string" ? payload.message.length : null,
    history_messages: Array.isArray(payload.messages) ? payload.messages.length : 0,
  });
  const emit = (event) => {
    lastEmitAt = Date.now();
    emitEvent?.(event);
  };
  const modelContext = buildSessionContext(buildSessionEntries(payload.session_messages || [], runtimeConfig));
  let initialMessages = modelContext.messages.length ? modelContext.messages : normalizeHistory(payload.messages);
  if (payload.session_messages?.length) {
    const compaction = await maybeCompactSessionHistory({
      sessionMessages: payload.session_messages,
      model,
      thinkingEffort: payload.thinking_effort,
      emitEvent: emit,
      signal: externalAbortSignal,
      auto: true,
      runtimeConfig,
    });
    initialMessages = compaction.contextMessages;
  }
  const agent = new Agent({
    initialState: {
      systemPrompt: buildSystemPrompt(runtimeConfig),
      model,
      thinkingLevel: mapThinkingLevel(payload.thinking_effort, model),
      tools: createTools(payload, runtimeConfig),
      messages: initialMessages,
    },
    getApiKey: () => runtimeConfig.apiKey,
    toolExecution: "sequential",
    transport: "auto",
    maxRetryDelayMs: 60000,
    transformContext: async (messages) =>
      MANAGER_COMPACTION_ENABLED ? messages : messages.slice(-CONTEXT_FALLBACK_MESSAGE_LIMIT),
  });
  agent.subscribe((event) => {
    lastAgentEventAt = Date.now();
    lastAgentEventType = event.type;
    events.push(event);
    if (event.type === "message_start" && event.message?.role === "assistant") {
      assistantTurnIndex += 1;
      currentAssistantTurnIndex = assistantTurnIndex;
    }
    if (event.type === "message_update" && event.assistantMessageEvent) {
      const assistantEvent = event.assistantMessageEvent;
      const turnIndex = currentAssistantTurnIndex >= 0 ? currentAssistantTurnIndex : 0;
      const blockKey = `${turnIndex}:${assistantEvent.contentIndex}`;
      if (assistantEvent.type === "thinking_start") {
        if (!thinkingBlocks.has(blockKey)) {
          thinkingBlocks.set(blockKey, "");
        }
        if (!thinkingStartedAt.has(blockKey)) {
          thinkingStartedAt.set(blockKey, Date.now());
        }
        emit({
          type: "thinking_start",
          content_index: assistantEvent.contentIndex,
          assistant_turn_index: turnIndex,
          started_at: thinkingStartedAt.get(blockKey),
        });
      } else if (assistantEvent.type === "thinking_delta") {
        const nextThinking = `${thinkingBlocks.get(blockKey) || ""}${assistantEvent.delta || ""}`;
        thinkingBlocks.set(blockKey, nextThinking);
        emit({
          type: "thinking_delta",
          delta: assistantEvent.delta || "",
          content_index: assistantEvent.contentIndex,
          assistant_turn_index: turnIndex,
        });
      } else if (assistantEvent.type === "thinking_end") {
        const finalizedThinking =
          typeof assistantEvent.content === "string"
            ? assistantEvent.content
            : thinkingBlocks.get(blockKey) || "";
        thinkingBlocks.set(blockKey, finalizedThinking);
        const endedAt = Date.now();
        emit({
          type: "thinking_end",
          content: finalizedThinking,
          content_index: assistantEvent.contentIndex,
          assistant_turn_index: turnIndex,
          started_at: thinkingStartedAt.get(blockKey),
          ended_at: endedAt,
        });
      } else if (assistantEvent.type === "text_delta") {
        const delta = assistantEvent.delta || "";
        streamedText += delta;
        emit({
          type: "text_delta",
          delta,
          content_index: assistantEvent.contentIndex,
          assistant_turn_index: turnIndex,
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
      const report = buildToolReport(event.toolName, details);
      if (report) {
        emit({
          type: "tool_report",
          tool_name: event.toolName,
          tool_call_id: event.toolCallId,
          summary: report.summary,
          details: report.details,
        });
      }
    }
    if (event.type === "message_end" && event.message?.role === "assistant") {
      finalAssistantMessage = event.message;
      const tokenUsage = normalizeTokenUsage(event.message.usage, model);
      if (tokenUsage) {
        finalTokenUsage = tokenUsage;
        emit({
          type: "usage",
          usage: tokenUsage,
        });
      }
    }
  });
  const userEnvelope = {
    user_request: payload.message,
    selected_context: payload.context || {},
    script_preference_guidance: scriptPreferenceGuidance(payload.context?.script_preference),
    runtime_preference_guidance: runtimePreferenceGuidance(payload.context || {}),
    instruction:
      "Answer naturally. Decide whether project tools are needed. If you change the blueprint, remember that cards are the blueprint units and use create_card, update_card, delete_card, configure_card_execution, run-control, or template tools directly as needed. After ok:false tool results, correct and retry when the fix is clear.",
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
    total_tokens: finalTokenUsage?.total_tokens,
  });
  return {
    message: text,
    thinking,
    proposal: null,
    actions: [],
    warnings: [],
    metadata: finalTokenUsage ? { token_usage: finalTokenUsage } : {},
  };
}

async function runManualCompaction(payload) {
  const runtimeConfig = resolveManagerConfig(payload);
  if (!runtimeConfig.apiKey) {
    throw new Error("MANAGER_AGENT_API_KEY or BLUEPRINT_DEEPSEEK_API_KEY is not configured.");
  }
  const model = resolveModel(runtimeConfig);
  if (!model) {
    throw new Error(`Manager model not found: provider=${runtimeConfig.provider}, model=${runtimeConfig.model}`);
  }
  const result = await maybeCompactSessionHistory({
    sessionMessages: payload.session_messages || [],
    model,
    thinkingEffort: payload.thinking_effort || "medium",
    signal: undefined,
    auto: false,
    force: true,
    runtimeConfig,
  });
  if (!result.compacted) {
    throw new Error("当前上下文还不需要压缩。");
  }
  return {
    compact_id: result.compactId,
    summary: result.summary,
    first_kept_message_id: result.firstKeptMessageId,
    tokens_before: result.tokensBefore,
    tokens_after: result.tokensAfter,
    duration_ms: result.durationMs,
    provider: result.provider,
    model: result.model,
  };
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
  if (req.method === "POST" && pathname === "/compact") {
    try {
      const payload = await readJson(req);
      const response = await runManualCompaction(payload);
      jsonResponse(res, 200, response);
    } catch (error) {
      jsonResponse(res, 502, { detail: error instanceof Error ? error.message : String(error) });
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
