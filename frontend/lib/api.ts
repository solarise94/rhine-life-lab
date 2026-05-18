import { Asset, AssetDetail, ProjectSnapshot, ReportSection, RunEvent, RuntimeApprovalDecision } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

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
    throw new Error(text || `API error: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getProject(projectId: string) {
    return request<ProjectSnapshot>(`/projects/${projectId}`);
  },
  getResults(projectId: string) {
    return request<{ accepted: Asset[]; candidate: Asset[]; other: Asset[] }>(`/projects/${projectId}/results`);
  },
  getResultAsset(projectId: string, assetId: string) {
    return request<AssetDetail>(`/projects/${projectId}/results/${assetId}`);
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
  sendChat(projectId: string, message: string) {
    return request<{ message: string; proposal?: unknown; actions: Array<{ label: string; action: string }> }>(
      `/projects/${projectId}/chat`,
      {
        method: "POST",
        body: JSON.stringify({ message, context: {} }),
      },
    );
  },
  acceptProposal(projectId: string, proposalId: string) {
    return request(`/projects/${projectId}/proposals/${proposalId}/accept`, { method: "POST" });
  },
  modifyProposal(projectId: string, proposalId: string, message: string) {
    return request<{ proposal: unknown; patch: unknown }>(`/projects/${projectId}/proposals/${proposalId}/modify`, {
      method: "POST",
      body: JSON.stringify({ message, context: {} }),
    });
  },
  rejectProposal(projectId: string, proposalId: string) {
    return request(`/projects/${projectId}/proposals/${proposalId}/reject`, { method: "POST" });
  },
  startRun(projectId: string, cardId: string, workerType?: string) {
    return request<{ run_id: string; card_id: string; status: string }>(`/projects/${projectId}/cards/${cardId}/start-run`, {
      method: "POST",
      body: JSON.stringify({ worker_type: workerType ?? null }),
    });
  },
  getRunEvents(projectId: string, runId: string) {
    return request<{ items: RunEvent[] }>(`/projects/${projectId}/runs/${runId}/events`);
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
