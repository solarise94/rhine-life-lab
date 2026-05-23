import {
  Asset,
  AssetDetail,
  AssetFlow,
  ChatSessionDetail,
  ChatSessionMessageRecord,
  ChatSessionSummary,
  ChatUploadResponse,
  CreateProjectPayload,
  Proposal,
  ProjectFiles,
  ProjectSnapshot,
  ProjectState,
  ProjectSummary,
  ReportSection,
  RunEvent,
  StartRunResponse,
  RuntimeApprovalDecision,
  WorkOrder,
} from "./types";
import type { ChatTokenUsage } from "./types";

export type { ChatTokenUsage } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

export interface ChatHistoryMessage {
  role: "user" | "manager";
  content: string;
}

export interface ChatRequestContext {
  selected_card_id?: string | null;
  selected_result_id?: string | null;
  script_preference?: "auto" | "prefer_python" | "prefer_r" | "prefer_mixed";
  python_runtime?: string | null;
  r_runtime?: string | null;
}

export type ChatStreamEvent =
  | { type: "thinking_start"; content_index?: number; assistant_turn_index?: number }
  | { type: "thinking_delta"; delta?: string; content_index?: number; assistant_turn_index?: number }
  | { type: "thinking_end"; content?: string; content_index?: number; assistant_turn_index?: number }
  | { type: "compact_start"; compact_id: string; auto?: boolean }
  | { type: "compact_delta"; compact_id: string; content?: string }
  | {
      type: "compact_end";
      compact_id: string;
      content?: string;
      duration_ms?: number;
      tokens_before?: number;
      tokens_after?: number;
      first_kept_message_id?: string;
      provider?: string;
      model?: string;
      auto?: boolean;
    }
  | { type: "compact_error"; compact_id: string; message?: string; auto?: boolean }
  | { type: "heartbeat"; stage?: string; message?: string }
  | { type: "text_delta"; delta?: string; content_index?: number; assistant_turn_index?: number }
  | { type: "usage"; usage?: ChatTokenUsage }
  | {
      type: "tool_start";
      tool_name?: string;
      tool_call_id?: string;
      label?: string;
      done_label?: string;
    }
  | {
      type: "tool_end";
      tool_name?: string;
      tool_call_id?: string;
      label?: string;
      done_label?: string;
      is_error?: boolean;
    }
  | { type: "proposal"; proposal?: unknown }
  | {
      type: "response";
      response?: {
        message: string;
        thinking?: string;
        proposal?: unknown;
        actions: Array<{ label: string; action: string }>;
        warnings: string[];
        metadata?: {
          token_usage?: ChatTokenUsage;
          [key: string]: unknown;
        };
      };
    }
  | { type: "done" }
  | { type: "error"; detail?: string };

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text();
    let parsedDetail: unknown = text;
    let detail = text;
    try {
      const payload = text ? (JSON.parse(text) as { detail?: unknown }) : null;
      parsedDetail = payload?.detail;
      if (typeof payload?.detail === "string" && payload.detail) {
        detail = payload.detail;
      } else if (payload?.detail && typeof payload.detail === "object") {
        const message = (payload.detail as { message?: string }).message;
        detail = message || JSON.stringify(payload.detail);
      }
    } catch {}
    throw new ApiError(response.status, detail || `API error: ${response.status}`, parsedDetail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listProjects() {
    return request<{ items: ProjectSummary[] }>("/projects");
  },
  createProject(payload: CreateProjectPayload) {
    return request<{ project: ProjectState }>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  deleteProject(projectId: string) {
    return request<{ ok: boolean }>(`/projects/${projectId}`, {
      method: "DELETE",
    });
  },
  getProject(projectId: string) {
    return request<ProjectSnapshot>(`/projects/${projectId}`);
  },
  getAssetFlow(projectId: string) {
    return request<AssetFlow>(`/projects/${projectId}/asset-flow`);
  },
  getWorkOrder(projectId: string) {
    return request<WorkOrder>(`/projects/${projectId}/work-order`);
  },
  getChatSessions(projectId: string) {
    return request<{ items: ChatSessionSummary[] }>(`/projects/${projectId}/chat-sessions`);
  },
  createChatSession(projectId: string, summary?: string) {
    return request<{ session: ChatSessionDetail }>(`/projects/${projectId}/chat-sessions`, {
      method: "POST",
      body: JSON.stringify({ summary: summary ?? null }),
    });
  },
  getChatSession(projectId: string, sessionId: string) {
    return request<{ session: ChatSessionDetail }>(`/projects/${projectId}/chat-sessions/${sessionId}`);
  },
  saveChatSession(projectId: string, sessionId: string, messages: ChatSessionMessageRecord[], summary?: string) {
    return request<{ session: ChatSessionDetail }>(`/projects/${projectId}/chat-sessions/${sessionId}`, {
      method: "PUT",
      body: JSON.stringify({ messages, summary: summary ?? null }),
    });
  },
  deleteChatSession(projectId: string, sessionId: string) {
    return request<{ ok: boolean }>(`/projects/${projectId}/chat-sessions/${sessionId}`, {
      method: "DELETE",
    });
  },
  async uploadChatFile(projectId: string, file: File) {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`${API_BASE}/projects/${projectId}/chat-uploads`, {
      method: "POST",
      body: formData,
      cache: "no-store",
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Upload failed: ${response.status}`);
    }
    return response.json() as Promise<ChatUploadResponse>;
  },
  getResults(projectId: string) {
    return request<{ accepted: Asset[]; candidate: Asset[]; other: Asset[] }>(`/projects/${projectId}/results`);
  },
  getFiles(projectId: string) {
    return request<ProjectFiles>(`/projects/${projectId}/files`);
  },
  deleteSessionUpload(projectId: string, assetId: string) {
    return request<{ ok: boolean; asset: Asset }>(`/projects/${projectId}/files/session-uploads/${assetId}`, {
      method: "DELETE",
    });
  },
  deleteDataAsset(projectId: string, assetId: string) {
    return request<{ ok: boolean; asset: Asset }>(`/projects/${projectId}/files/assets/${assetId}`, {
      method: "DELETE",
    });
  },
  getResultAsset(projectId: string, assetId: string) {
    return request<AssetDetail>(`/projects/${projectId}/results/${assetId}`);
  },
  compactChatSession(
    projectId: string,
    sessionMessages: ChatSessionMessageRecord[],
    thinkingEffort: "low" | "medium" | "high" = "medium",
  ) {
    return request<{
      compact_id: string;
      summary: string;
      first_kept_message_id: string;
      tokens_before: number;
      tokens_after: number;
      duration_ms: number;
      provider?: string | null;
      model?: string | null;
    }>(`/projects/${projectId}/chat-compact`, {
      method: "POST",
      body: JSON.stringify({
        message: "/compact",
        context: {},
        thinking_effort: thinkingEffort,
        messages: [],
        session_messages: sessionMessages,
      }),
    });
  },
  getReport(projectId: string) {
    return request<{ project: unknown; sections: ReportSection[] }>(`/projects/${projectId}/report`);
  },
  getAdvancedGraph(projectId: string) {
    return request<{ graph: Record<string, unknown>; cards: unknown[] }>(`/projects/${projectId}/advanced/graph`);
  },
  getAdvancedGit(projectId: string) {
    return request<{ items: Array<{ hash: string; date: string; subject: string }> }>(`/projects/${projectId}/advanced/git`);
  },
  getAdvancedProposals(projectId: string) {
    return request<{ items: unknown[] }>(`/projects/${projectId}/advanced/proposals`);
  },
  sendChat(projectId: string, message: string, messages: ChatHistoryMessage[] = [], context: ChatRequestContext = {}) {
    return request<{ message: string; thinking?: string; proposal?: unknown; actions: Array<{ label: string; action: string }> }>(
      `/projects/${projectId}/chat`,
      {
        method: "POST",
        body: JSON.stringify({ message, context, thinking_effort: "medium", messages }),
      },
    );
  },
  async streamChat(
    projectId: string,
    message: string,
    thinkingEffort: "low" | "medium" | "high" = "medium",
    messages: ChatHistoryMessage[] = [],
    sessionMessages: ChatSessionMessageRecord[] = [],
    onEvent?: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
    context: ChatRequestContext = {},
  ) {
    const response = await fetch(`${API_BASE}/projects/${projectId}/chat-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        context,
        thinking_effort: thinkingEffort,
        messages,
        session_messages: sessionMessages,
      }),
      cache: "no-store",
      signal,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Chat stream failed: ${response.status}`);
    }
    if (!response.body) {
      throw new Error("Chat stream is not available.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const flushBuffer = () => {
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + (buffer[boundary] === "\r" ? 4 : 2));
        const payload = rawEvent
          .split(/\r?\n/)
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n")
          .trim();
        if (payload) {
          onEvent?.(JSON.parse(payload) as ChatStreamEvent);
        }
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      flushBuffer();
    }
    buffer += decoder.decode();
    flushBuffer();
  },
  createChatJob(
    projectId: string,
    message: string,
    thinkingEffort: "low" | "medium" | "high" = "medium",
    messages: ChatHistoryMessage[] = [],
    context: ChatRequestContext = {},
  ) {
    return request<{ job_id: string; status: string }>(`/projects/${projectId}/chat-jobs`, {
      method: "POST",
      body: JSON.stringify({ message, context, thinking_effort: thinkingEffort, messages }),
    });
  },
  getChatJob(projectId: string, jobId: string) {
    return request<{
      job_id: string;
      status: "queued" | "running" | "succeeded" | "failed";
      response: { message: string; thinking?: string; proposal?: unknown; actions: Array<{ label: string; action: string }> } | null;
      error: string | null;
    }>(`/projects/${projectId}/chat-jobs/${jobId}`);
  },
  acceptProposal(projectId: string, proposalId: string) {
    return request<{ proposal: Proposal; apply_result: unknown; snapshot: ProjectSnapshot }>(
      `/projects/${projectId}/proposals/${proposalId}/accept`,
      { method: "POST" },
    );
  },
  modifyProposal(projectId: string, proposalId: string, message: string) {
    return request<{ proposal: Proposal; patch: unknown }>(`/projects/${projectId}/proposals/${proposalId}/modify`, {
      method: "POST",
      body: JSON.stringify({ message, context: {} }),
    });
  },
  rejectProposal(projectId: string, proposalId: string) {
    return request<{ proposal: Proposal }>(`/projects/${projectId}/proposals/${proposalId}/reject`, { method: "POST" });
  },
  startRun(projectId: string, cardId: string, workerType?: string, pythonRuntime?: string, rRuntime?: string) {
    return request<StartRunResponse>(`/projects/${projectId}/cards/${cardId}/start-run`, {
      method: "POST",
      body: JSON.stringify({ worker_type: workerType ?? null, python_runtime: pythonRuntime ?? null, r_runtime: rRuntime ?? null }),
    });
  },
  resetCardRunState(projectId: string, cardId: string) {
    return request<{ card_id: string; status: string }>(`/projects/${projectId}/cards/${cardId}/reset-run-state`, {
      method: "POST",
    });
  },
  rerunCard(projectId: string, cardId: string, workerType?: string, pythonRuntime?: string, rRuntime?: string) {
    return request<StartRunResponse>(`/projects/${projectId}/cards/${cardId}/rerun`, {
      method: "POST",
      body: JSON.stringify({ worker_type: workerType ?? null, python_runtime: pythonRuntime ?? null, r_runtime: rRuntime ?? null }),
    });
  },
  getRunEvents(projectId: string, runId: string) {
    return request<{ items: RunEvent[] }>(`/projects/${projectId}/runs/${runId}/events`);
  },
  cancelRun(projectId: string, runId: string, reason?: string) {
    return request<{ run_id: string; status: string; summary: string }>(`/projects/${projectId}/runs/${runId}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    });
  },
  cleanupRun(projectId: string, runId: string, reason?: string) {
    return request<{ run_id: string; cleanup_status: string; archived_at?: string | null }>(
      `/projects/${projectId}/runs/${runId}/cleanup`,
      {
        method: "POST",
        body: JSON.stringify({ reason: reason ?? null }),
      },
    );
  },
  getRuntimeApprovals(projectId: string, runId: string) {
    return request<{ items: RuntimeApprovalDecision[] }>(`/projects/${projectId}/runs/${runId}/runtime-approvals`);
  },
  decideRuntimeApproval(projectId: string, runId: string, requestId: string, approve: boolean) {
    return request(`/projects/${projectId}/runs/${runId}/runtime-approvals/${requestId}`, {
      method: "POST",
      body: JSON.stringify({ approve }),
    });
  },
  reviewRun(projectId: string, runId: string, accept = true) {
    return request(`/projects/${projectId}/runs/${runId}/review`, {
      method: "POST",
      body: JSON.stringify({ accept }),
    });
  },
  getManifest(projectId: string, runId: string) {
    return request<{ manifest: unknown; valid: boolean; errors: string[] }>(`/projects/${projectId}/runs/${runId}/manifest`);
  },
  reorderReport(projectId: string, itemIds: string[]) {
    return request<{ project: unknown; sections: ReportSection[] }>(`/projects/${projectId}/report/reorder`, {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds }),
    });
  },
  exportReportHtml(projectId: string) {
    return request<{ path: string; html: string }>(`/projects/${projectId}/report/export-html`, { method: "POST" });
  },
  getResultAssetContentUrl(projectId: string, assetId: string) {
    return `${API_BASE}/projects/${projectId}/results/${assetId}/content`;
  },
  getExecutionFileContentUrl(projectId: string, path: string) {
    return `${API_BASE}/projects/${projectId}/files/content?path=${encodeURIComponent(path)}`;
  },
  getRunEventsWsUrl(projectId: string, runId: string) {
    if (typeof window === "undefined") {
      return "";
    }
    if (!API_BASE.startsWith("http")) {
      return "";
    }
    const base = new URL(API_BASE);
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = `${base.pathname.replace(/\/$/, "")}/projects/${projectId}/runs/${runId}/ws`;
    base.search = "";
    return base.toString();
  },
};
