import { Agent } from "@earendil-works/pi-agent-core";
import { getModel, registerBuiltInApiProviders, Type } from "@earendil-works/pi-ai";

registerBuiltInApiProviders();

const HOST = process.env.MANAGER_AGENT_HOST || "127.0.0.1";
const PORT = Number(process.env.MANAGER_AGENT_PORT || "18002");
const PROVIDER = process.env.MANAGER_AGENT_PROVIDER || "deepseek";
const MODEL = process.env.MANAGER_AGENT_MODEL || process.env.BLUEPRINT_MANAGER_MODEL || "deepseek-v4-pro";
const API_KEY = process.env.MANAGER_AGENT_API_KEY || process.env.BLUEPRINT_DEEPSEEK_API_KEY || "";
const TIMEOUT_MS = Number(process.env.MANAGER_AGENT_TIMEOUT_MS || "600000");

const SYSTEM_PROMPT = `You are the Manager AI for Blueprint RE, a bioinformatics workflow manager.

You are an interactive chat agent. Answer normal questions directly. Use tools when you need project context or when the user asks to change the blueprint.

Hard permissions:
- You may read blueprint context through tools.
- You may create auditable proposals through tools.
- You may modify existing blueprint proposals through proposal tools.
- You may delete blueprint modules only by creating a proposal that marks modules/cards cancelled.
- You may restore cancelled blueprint modules only by creating a proposal.
- You may read result assets through read_result_asset, but not arbitrary files.
- You must never claim a change was applied. The user must accept a proposal in the UI.
- You must not run shell commands, write scripts, execute analyses, or edit files.
- If a tool fails validation, inspect the error, correct your arguments, and retry when possible.

Blueprint mutation rules:
- For add/update proposals, call draft_blueprint_proposal with valid patch ops.
- For delete, prefer delete_blueprint_module with the exact module_id from context.
- For restore, prefer restore_blueprint_module with the exact module_id from context.
- For proposal edits, call modify_blueprint_proposal with the exact proposal_id.
- For result interpretation, call read_result_asset with the exact asset_id from context.
- Do not invent existing ids. Call get_project_context first if ids are uncertain.
- Keep final replies concise and user-facing.`;

function jsonResponse(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
  });
  res.end(body);
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
      description: "Read the current Blueprint project, modules, cards, assets, runs, and proposals. This is read-only.",
      parameters: Type.Object({}),
      execute: async (_toolCallId, _params, signal) => {
        const payload = await callBackend(baseUrl, token, `/internal/manager-tools/projects/${projectId}/context`, { signal });
        return textResult(JSON.stringify(payload, null, 2), payload);
      },
    },
    {
      name: "draft_blueprint_proposal",
      label: "Draft blueprint proposal",
      description: "Create an auditable proposal for adding or modifying blueprint modules/cards. This never applies the change.",
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
      description: "Create an auditable proposal to delete/cancel a module by module_id. This never applies the change.",
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
      name: "restore_blueprint_module",
      label: "Draft module restore",
      description: "Create an auditable proposal to restore a cancelled module/card by module_id. This never applies the change.",
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
      name: "modify_blueprint_proposal",
      label: "Modify blueprint proposal",
      description: "Replace an existing proposal with a validated structured proposal draft. This updates the proposal only and never applies it.",
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
      description: "Read a whitelisted result asset detail/preview by asset_id. This is read-only and cannot read arbitrary paths.",
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

async function runManagerChat(payload) {
  if (!API_KEY) {
    throw new Error("MANAGER_AGENT_API_KEY or BLUEPRINT_DEEPSEEK_API_KEY is not configured.");
  }
  const events = [];
  let finalAssistantMessage = null;
  const agent = new Agent({
    initialState: {
      systemPrompt: SYSTEM_PROMPT,
      model: getModel(PROVIDER, MODEL),
      thinkingLevel: "medium",
      tools: createTools(payload),
      messages: [],
    },
    getApiKey: () => API_KEY,
    toolExecution: "sequential",
    transport: "auto",
    maxRetryDelayMs: 60000,
    transformContext: async (messages) => messages.slice(-30),
  });
  agent.subscribe((event) => {
    events.push(event);
    if (event.type === "message_end" && event.message?.role === "assistant") {
      finalAssistantMessage = event.message;
    }
  });
  const userEnvelope = {
    user_request: payload.message,
    selected_context: payload.context || {},
    instruction: "Use get_project_context when project state is needed. For blueprint changes, use proposal tools only.",
  };
  const abort = AbortSignal.timeout(TIMEOUT_MS);
  const run = agent.prompt(JSON.stringify(userEnvelope, null, 2));
  abort.addEventListener("abort", () => agent.abort(), { once: true });
  await run;
  const proposalPayload = latestToolProposal(events);
  if (proposalPayload) {
    const fallbackMessage = proposalPayload.message || proposalPayload.proposal?.summary || "已生成可审核 proposal。";
    return {
      message: extractText(finalAssistantMessage).trim() || fallbackMessage,
      proposal: proposalPayload.proposal || null,
      actions: proposalPayload.actions || [],
      warnings: proposalPayload.warnings || [],
    };
  }
  const text = extractText(finalAssistantMessage).trim();
  const stopReason = finalAssistantMessage?.stopReason;
  if (stopReason === "error" || stopReason === "aborted") {
    throw new Error(finalAssistantMessage.errorMessage || `Manager agent stopped with ${stopReason}`);
  }
  if (!text) {
    throw new Error("Manager agent returned an empty response.");
  }
  return { message: text, proposal: null, actions: [], warnings: [] };
}

async function handle(req, res) {
  if (req.method === "GET" && req.url === "/healthz") {
    jsonResponse(res, 200, { status: "ok" });
    return;
  }
  if (req.method !== "POST" || req.url !== "/chat") {
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
