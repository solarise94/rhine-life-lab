"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Loader2,
  Paperclip,
  Pencil,
  Send,
  Square,
  Sparkles,
  X,
} from "lucide-react";

import { api, ChatHistoryMessage, ChatStreamEvent } from "@/lib/api";
import { useChatSession, useModifyProposalMutation } from "@/lib/hooks";
import { queryKeys } from "@/lib/query-keys";
import { Asset, ChatSessionDetail, ChatSessionMessageRecord, ProjectSnapshot, Proposal } from "@/lib/types";
import { EMPTY_ATTACHMENTS, useWorkspaceUiStore } from "@/lib/stores/workspace-ui-store";

type ThinkingEffort = "low" | "medium" | "high";
type ToolState = "running" | "done" | "error";

interface ToolUseState {
  id: string;
  toolName?: string;
  label: string;
  status: ToolState;
}

interface ChatMessage {
  id: string;
  role: "user" | "manager";
  content: string;
  proposal?: Proposal;
  thinking?: string;
  thinkingState?: "idle" | "running" | "done" | "error";
  tools?: ToolUseState[];
  state?: "idle" | "thinking" | "streaming" | "done" | "error";
}

interface ProposalMutationResponse {
  proposal?: Proposal;
  snapshot?: ProjectSnapshot;
}

interface MentionState {
  query: string;
  start: number;
  end: number;
}

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const DEFAULT_MANAGER_MESSAGE: ChatMessage = {
  id: "welcome",
  role: "manager",
  state: "done",
  content: "可以先正常聊天和查看上下文；当你明确要求调整蓝图时，我会通过后端工具直接读写 cards，并按数据资产时间线校验。",
};

function isProposalShape(value: unknown): value is Proposal {
  if (!value || typeof value !== "object") {
    return false;
  }
  const proposal = value as Record<string, unknown>;
  return (
    typeof proposal.proposal_id === "string" &&
    proposal.proposal_id.length > 0 &&
    typeof proposal.patch_id === "string" &&
    typeof proposal.title === "string" &&
    typeof proposal.summary === "string" &&
    typeof proposal.impact_summary === "string" &&
    typeof proposal.status === "string" &&
    Array.isArray(proposal.consistency_warnings) &&
    typeof proposal.created_at === "string" &&
    typeof proposal.updated_at === "string"
  );
}

function parseProposal(value: unknown): Proposal | null {
  if (isProposalShape(value)) {
    return value;
  }
  if (value && typeof value === "object" && "proposal" in value) {
    const nested = (value as { proposal?: unknown }).proposal;
    if (isProposalShape(nested)) {
      return nested;
    }
  }
  return null;
}

function isAbortLikeError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return /abort|aborted|client_disconnected/i.test(`${error.name} ${error.message}`);
}

function settleInterruptedTools(tools?: ToolUseState[]) {
  return (tools ?? []).map((tool) =>
    tool.status === "running"
      ? {
          ...tool,
          status: "error" as const,
        }
      : tool,
  );
}

function normalizeSessionMessages(messages: ChatSessionMessageRecord[]): ChatMessage[] {
  return messages
    .filter((message) => Boolean(message?.id) && (message.role === "user" || message.role === "manager"))
    .map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      proposal: message.proposal ?? undefined,
      thinking: message.thinking ?? undefined,
      state: message.state ?? "done",
      thinkingState: message.thinking ? "done" : "idle",
    }));
}

function serializeSessionMessages(messages: ChatMessage[]): ChatSessionMessageRecord[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: message.content,
    proposal: message.proposal ?? null,
    thinking: message.thinking ?? null,
    state: message.state ?? "done",
  }));
}

function sessionMessagesSignature(messages: ChatMessage[]): string {
  return JSON.stringify(
    messages.map((message) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      proposal_id: message.proposal?.proposal_id ?? null,
      proposal_status: message.proposal?.status ?? null,
      thinking: message.thinking ?? null,
      state: message.state ?? null,
    })),
  );
}

function upsertSessionSummary(
  items: ChatSessionDetail[] | Array<{ session_id: string; summary: string; created_at: string; updated_at: string; message_count: number }> | undefined,
  session: ChatSessionDetail,
) {
  const next = [...(items ?? [])];
  const summary = {
    session_id: session.session_id,
    summary: session.summary,
    created_at: session.created_at,
    updated_at: session.updated_at,
    message_count: session.messages.length,
  };
  const index = next.findIndex((item) => item.session_id === session.session_id);
  if (index >= 0) {
    next[index] = summary;
  } else {
    next.push(summary);
  }
  return next.sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function ManagerChatPanel({
  projectId,
  sessionId,
  proposals = [],
  mentionableAssets,
  onRefresh,
}: {
  projectId: string;
  sessionId?: string | null;
  proposals?: Proposal[];
  mentionableAssets: Asset[];
  onRefresh: () => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [thinkingEffort, setThinkingEffort] = useState<ThinkingEffort>("medium");
  const chatSessionQuery = useChatSession(projectId, sessionId ?? undefined, Boolean(sessionId));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingProposalId, setEditingProposalId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [mentionState, setMentionState] = useState<MentionState | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const thinkingRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const refreshTimerRef = useRef<number | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const activeStreamControllerRef = useRef<AbortController | null>(null);
  const stopRequestedRef = useRef(false);
  const currentSessionIdRef = useRef<string | null>(sessionId ?? null);
  const hydratedSessionIdRef = useRef<string | null>(null);
  const lastSavedSignatureRef = useRef("[]");

  const attachments = useWorkspaceUiStore((s) => s.attachmentsByProject[projectId] ?? EMPTY_ATTACHMENTS);
  const addAttachment = useWorkspaceUiStore((s) => s.addAttachment);
  const removeAttachment = useWorkspaceUiStore((s) => s.removeAttachment);
  const clearAttachments = useWorkspaceUiStore((s) => s.clearAttachments);
  const draftMessage = useWorkspaceUiStore((s) => s.draftMessageByProject[projectId] ?? "");
  const clearDraftMessage = useWorkspaceUiStore((s) => s.clearDraftMessage);
  const saveSessionMutation = useMutation({
    mutationFn: ({
      targetSessionId,
      nextMessages,
    }: {
      targetSessionId: string;
      nextMessages: ChatMessage[];
    }) =>
      api.saveChatSession(projectId, targetSessionId, serializeSessionMessages(nextMessages)),
    onSuccess: ({ session }, variables) => {
      if (currentSessionIdRef.current !== variables.targetSessionId) {
        return;
      }
      lastSavedSignatureRef.current = sessionMessagesSignature(variables.nextMessages);
      queryClient.setQueryData(queryKeys.chatSession(projectId, session.session_id), { session });
      queryClient.setQueryData(
        queryKeys.chatSessions(projectId),
        (
          previous:
            | { items: Array<{ session_id: string; summary: string; created_at: string; updated_at: string; message_count: number }> }
            | undefined,
        ) => ({
          items: upsertSessionSummary(previous?.items, session),
        }),
      );
    },
    onError: (nextError, variables) => {
      const message = nextError instanceof Error ? nextError.message : "Session save failed.";
      const staleSessionSave =
        currentSessionIdRef.current !== variables.targetSessionId ||
        /Chat session not found/i.test(message);
      if (staleSessionSave) {
        return;
      }
      setError(message);
    },
  });
  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadChatFile(projectId, file),
    onSuccess: async (response) => {
      addAttachment(projectId, response.attachment);
      setError(null);
      await onRefresh();
    },
    onError: (nextError) => {
      setError(nextError instanceof Error ? nextError.message : "Upload failed.");
    },
  });
  const acceptProposalMutation = useMutation({
    mutationFn: (proposalId: string) => api.acceptProposal(projectId, proposalId),
  });
  const rejectProposalMutation = useMutation({
    mutationFn: (proposalId: string) => api.rejectProposal(projectId, proposalId),
  });
  const modifyProposalMutation = useModifyProposalMutation(projectId);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, error, busy, attachments, chatSessionQuery.isLoading]);

  useEffect(() => {
    if (draftMessage) {
      setDraft((prev) => (prev ? prev + "\n" : "") + draftMessage);
      clearDraftMessage(projectId);
    }
  }, [draftMessage, projectId, clearDraftMessage]);

  useEffect(() => {
    setMentionIndex(0);
  }, [mentionState?.query]);

  useEffect(() => {
    currentSessionIdRef.current = sessionId ?? null;
    activeStreamControllerRef.current?.abort(new Error("session_changed"));
    activeStreamControllerRef.current = null;
    stopRequestedRef.current = false;
    if (saveTimerRef.current !== null && typeof window !== "undefined") {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    hydratedSessionIdRef.current = null;
    lastSavedSignatureRef.current = "[]";
    setBusy(false);
    setError(null);
    setEditingProposalId(null);
    setEditDraft("");
    setMentionState(null);
    setMentionIndex(0);
    setMessages([]);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || !chatSessionQuery.data?.session || activeStreamControllerRef.current) {
      return;
    }
    const nextMessages = normalizeSessionMessages(chatSessionQuery.data.session.messages);
    const nextSignature = sessionMessagesSignature(nextMessages);
    if (hydratedSessionIdRef.current === sessionId && nextSignature === lastSavedSignatureRef.current) {
      lastSavedSignatureRef.current = nextSignature;
      return;
    }
    hydratedSessionIdRef.current = sessionId;
    lastSavedSignatureRef.current = nextSignature;
    setMessages(nextMessages);
    setError(null);
  }, [chatSessionQuery.data, sessionId]);

  useEffect(() => {
    if (!sessionId || hydratedSessionIdRef.current !== sessionId || typeof window === "undefined") {
      return;
    }
    const signature = sessionMessagesSignature(messages);
    if (signature === lastSavedSignatureRef.current) {
      return;
    }
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      saveSessionMutation.mutate({ targetSessionId: sessionId, nextMessages: messages });
    }, 600);
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = null;
      }
    };
  }, [messages, projectId, saveSessionMutation, sessionId]);

  useEffect(() => () => {
    if (saveTimerRef.current !== null && typeof window !== "undefined") {
      window.clearTimeout(saveTimerRef.current);
    }
    if (refreshTimerRef.current !== null && typeof window !== "undefined") {
      window.clearTimeout(refreshTimerRef.current);
    }
    activeStreamControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    messages.forEach((message) => {
      if (message.role !== "manager" || (!message.thinking && message.thinkingState !== "running")) {
        return;
      }
      const element = thinkingRefs.current[message.id];
      if (element) {
        element.scrollTop = element.scrollHeight;
      }
    });
  }, [messages]);

  function createMessageId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
      return crypto.randomUUID();
    }
    return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  const mentionOptions = useMemo(() => {
    const query = mentionState?.query.trim().toLowerCase() ?? "";
    // Keep the picker audit-friendly for now: users may explicitly reference stale or superseded assets in chat.
    return mentionableAssets
      .filter((asset) => !query || asset.title.toLowerCase().includes(query) || asset.asset_id.toLowerCase().includes(query))
      .sort((left, right) => left.title.localeCompare(right.title))
      .slice(0, 8);
  }, [mentionState?.query, mentionableAssets]);

  function syncMentionState(text: string, selectionStart: number | null) {
    setMentionState(getMentionState(text, selectionStart ?? text.length));
  }

  function insertMention(asset: Asset) {
    const activeMention = mentionState;
    const current = textareaRef.current;
    if (!activeMention || !current) {
      addAttachment(projectId, { type: "asset", id: asset.asset_id, label: asset.title });
      return;
    }
    const nextDraft = `${draft.slice(0, activeMention.start)}@${asset.title} ${draft.slice(activeMention.end)}`;
    const nextCursor = activeMention.start + asset.title.length + 2;
    setDraft(nextDraft);
    addAttachment(projectId, { type: "asset", id: asset.asset_id, label: asset.title });
    setMentionState(null);
    setMentionIndex(0);
    window.requestAnimationFrame(() => {
      current.focus();
      current.setSelectionRange(nextCursor, nextCursor);
    });
  }

  function updateMessage(messageId: string, updater: (message: ChatMessage) => ChatMessage) {
    setMessages((previous) => previous.map((message) => (message.id === messageId ? updater(message) : message)));
  }

  function buildChatHistory(nextUserText: string): ChatHistoryMessage[] {
    return [
      ...messages
        .filter((message) => message.role === "user" || message.role === "manager")
        .map((message) => ({
          role: message.role,
          content: message.content,
        })),
      { role: "user", content: nextUserText },
    ];
  }

  function schedulePartialRefresh() {
    if (refreshTimerRef.current !== null && typeof window !== "undefined") {
      window.clearTimeout(refreshTimerRef.current);
    }
    if (typeof window === "undefined") {
      return;
    }
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      void Promise.all([
        queryClient.refetchQueries({ queryKey: queryKeys.project(projectId), type: "active" }),
        queryClient.refetchQueries({ queryKey: queryKeys.workOrder(projectId), type: "active" }),
        queryClient.refetchQueries({ queryKey: queryKeys.advancedProposals(projectId), type: "active" }),
      ]);
    }, 120);
  }

  function syncProposal(proposal: Proposal) {
    if (!proposal.proposal_id || !proposal.status) {
      return;
    }
    queryClient.setQueryData<ProjectSnapshot>(queryKeys.project(projectId), (previous) => {
      if (!previous) return previous;
      const proposals = [...previous.proposals];
      const index = proposals.findIndex((item) => item.proposal_id === proposal.proposal_id);
      if (index >= 0) {
        proposals[index] = proposal;
      } else {
        proposals.push(proposal);
      }
      return { ...previous, proposals };
    });
    queryClient.setQueryData<{ items: Proposal[] }>(queryKeys.advancedProposals(projectId), (previous) => {
      const items = [...(previous?.items ?? [])];
      const index = items.findIndex((item) => item.proposal_id === proposal.proposal_id);
      if (index >= 0) {
        items[index] = proposal;
      } else {
        items.push(proposal);
      }
      return { items };
    });
    schedulePartialRefresh();
  }

  function syncSnapshot(snapshot: ProjectSnapshot) {
    queryClient.setQueryData(queryKeys.project(projectId), snapshot);
    queryClient.setQueryData(queryKeys.advancedProposals(projectId), { items: snapshot.proposals });
  }

  function findToolIndex(tools: ToolUseState[], toolCallId?: string, toolName?: string) {
    if (toolCallId) {
      return tools.findIndex((tool) => tool.id === toolCallId);
    }
    for (let index = tools.length - 1; index >= 0; index -= 1) {
      if (tools[index].toolName === toolName) {
        return index;
      }
    }
    return -1;
  }

  function upsertTool(tools: ToolUseState[], event: Extract<ChatStreamEvent, { type: "tool_start" | "tool_end" }>) {
    const nextTools = [...tools];
    const toolId = event.tool_call_id || `tool_${event.tool_name || "unknown"}`;
    const existingIndex = findToolIndex(nextTools, event.tool_call_id, event.tool_name);
    const nextTool: ToolUseState = {
      id: existingIndex >= 0 ? nextTools[existingIndex].id : toolId,
      toolName: event.tool_name,
      label: event.label || event.done_label || event.tool_name || "Tool",
      status: event.type === "tool_end" ? (event.is_error ? "error" : "done") : "running",
    };
    if (existingIndex >= 0) {
      nextTools[existingIndex] = {
        ...nextTools[existingIndex],
        ...nextTool,
        label: nextTool.label || nextTools[existingIndex].label,
      };
      return nextTools;
    }
    nextTools.push(nextTool);
    return nextTools;
  }

  function applyStreamEvent(messageId: string, event: ChatStreamEvent) {
    updateMessage(messageId, (current) => {
      switch (event.type) {
        case "thinking_start":
          return {
            ...current,
            thinkingState: "running",
            thinking: current.thinking ?? "",
            state: current.content ? "streaming" : "thinking",
          };
        case "thinking_delta":
          return {
            ...current,
            thinkingState: "running",
            thinking: `${current.thinking || ""}${event.delta || ""}`,
            state: current.content ? "streaming" : "thinking",
          };
        case "thinking_end":
          return {
            ...current,
            thinkingState: current.thinking || event.content ? "done" : current.thinkingState,
            thinking: event.content?.trim() || current.thinking,
          };
        case "heartbeat":
          return {
            ...current,
            thinkingState: current.state === "done" ? current.thinkingState : "running",
            thinking: current.thinking || event.message || "Manager 正在生成回复…",
            state: current.content ? current.state : "thinking",
          };
        case "text_delta":
          return {
            ...current,
            state: "streaming",
            content: `${current.content}${event.delta || ""}`,
          };
        case "tool_start":
          return {
            ...current,
            tools: upsertTool(current.tools || [], event),
            state: current.content ? "streaming" : "thinking",
          };
        case "tool_end":
          if (!event.is_error && event.tool_name && /blueprint_proposal|blueprint_module|blueprint_card/.test(event.tool_name)) {
            schedulePartialRefresh();
          }
          return {
            ...current,
            tools: upsertTool(current.tools || [], event),
          };
        case "proposal":
          {
            const proposal = parseProposal(event.proposal);
            if (proposal) {
              syncProposal(proposal);
            }
            return {
              ...current,
              proposal: proposal || current.proposal,
            };
          }
        case "response":
          {
            const proposal = parseProposal(event.response?.proposal);
            if (proposal) {
              syncProposal(proposal);
            }
            return {
              ...current,
              state: "done",
              content: event.response?.message || current.content,
              thinking: event.response?.thinking?.trim() || current.thinking,
              thinkingState: current.thinking || event.response?.thinking ? "done" : current.thinkingState,
              proposal: proposal || current.proposal,
            };
          }
        case "done":
          return {
            ...current,
            state: current.state === "error" ? "error" : "done",
            thinkingState: current.thinkingState === "running" ? "done" : current.thinkingState,
          };
        case "error":
          return {
            ...current,
            state: "error",
            thinkingState: current.thinkingState === "running" ? "error" : current.thinkingState,
            content: current.content || "请求失败。",
          };
        default:
          return current;
      }
    });
  }

  async function submit() {
    if (!draft.trim() || busy || !sessionId) return;
    const text = draft.trim();
    const context = attachments.length
      ? `上下文附件: ${attachments.map((a) => `[${a.type === "card" ? "卡片" : "资产"}: ${a.label}; id=${a.id}]`).join(" ")}\n\n${text}`
      : text;
    const history = buildChatHistory(text);
    const userMessageId = createMessageId();
    const managerMessageId = createMessageId();
    setMessages((prev) => [
      ...prev,
      { id: userMessageId, role: "user", content: text, state: "done" },
      { id: managerMessageId, role: "manager", content: "", thinking: "", thinkingState: "idle", tools: [], state: "thinking" },
    ]);
    setDraft("");
    setBusy(true);
    setError(null);
    stopRequestedRef.current = false;
    const abortController = new AbortController();
    activeStreamControllerRef.current = abortController;
    try {
      let streamError: string | null = null;
      await api.streamChat(
        projectId,
        context,
        thinkingEffort,
        history,
        (event) => {
          applyStreamEvent(managerMessageId, event);
          if (event.type === "error") {
            streamError = event.detail || "Chat failed.";
          }
        },
        abortController.signal,
      );
      if (streamError) {
        throw new Error(streamError);
      }
      clearAttachments(projectId);
      await onRefresh();
    } catch (nextError) {
      if (stopRequestedRef.current || isAbortLikeError(nextError)) {
        setError(null);
        updateMessage(managerMessageId, (current) => ({
          ...current,
          state: "done",
          tools: settleInterruptedTools(current.tools),
          thinkingState: current.thinkingState === "running" ? "done" : current.thinkingState,
          content: current.content || "已停止本次生成。",
          thinking: current.thinking,
        }));
        return;
      }
      const message = nextError instanceof Error ? nextError.message : "Chat failed.";
      setError(message);
      updateMessage(managerMessageId, (current) => ({
        ...current,
        state: "error",
        thinkingState: current.thinkingState === "running" ? "error" : current.thinkingState,
        content: current.content || "请求失败。",
        thinking: current.thinking,
      }));
    } finally {
      activeStreamControllerRef.current = null;
      stopRequestedRef.current = false;
      setBusy(false);
    }
  }

  function stopGeneration() {
    if (!busy || !activeStreamControllerRef.current) {
      return;
    }
    stopRequestedRef.current = true;
    activeStreamControllerRef.current.abort(new Error("user_aborted"));
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      setError("文件超过 50MB，当前上传入口不支持。");
      return;
    }
    uploadMutation.mutate(file);
  }

  async function accept(proposalId: string) {
    setBusy(true);
    setError(null);
    try {
      const response = (await acceptProposalMutation.mutateAsync(proposalId)) as ProposalMutationResponse;
      if (response.snapshot) {
        syncSnapshot(response.snapshot);
      } else if (response.proposal) {
        syncProposal(response.proposal);
      }
      if (response.proposal) {
        setMessages((prev) =>
          prev.map((m) =>
            m.proposal?.proposal_id === proposalId ? { ...m, proposal: response.proposal! } : m,
          ),
        );
      }
      await onRefresh();
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Accept failed.";
      if (message.includes("cannot accept")) {
        setError(`该 proposal 当前已不是 proposed 状态：${message}`);
      } else if (message.includes("409")) {
        setError(`提案应用时发生冲突：${message}`);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function reject(proposalId: string) {
    setBusy(true);
    setError(null);
    try {
      const response = (await rejectProposalMutation.mutateAsync(proposalId)) as ProposalMutationResponse;
      if (response.proposal) {
        syncProposal(response.proposal);
      }
      if (response.proposal) {
        setMessages((prev) =>
          prev.map((m) =>
            m.proposal?.proposal_id === proposalId ? { ...m, proposal: response.proposal! } : m,
          ),
        );
      }
      await onRefresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Reject failed.");
    } finally {
      setBusy(false);
    }
  }

  async function modify(proposalId: string) {
    if (!editDraft.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const response = await modifyProposalMutation.mutateAsync({
        proposalId,
        message: editDraft.trim(),
      });
      const proposal = response.proposal;
      syncProposal(proposal);
      setMessages((prev) => [
        ...prev,
        { id: createMessageId(), role: "user", content: `修改提案：${editDraft.trim()}`, state: "done" },
        { id: createMessageId(), role: "manager", content: proposal.summary, proposal, state: "done" },
      ]);
      setEditingProposalId(null);
      setEditDraft("");
      await onRefresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Modify failed.");
    } finally {
      setBusy(false);
    }
  }

  const openProposals = useMemo(() => {
    const messageProposalIds = new Set(messages.filter((m) => m.proposal).map((m) => m.proposal!.proposal_id));
    return proposals.filter((p) => p.status === "proposed" && !messageProposalIds.has(p.proposal_id));
  }, [proposals, messages]);
  const displayMessages = messages.length ? messages : [DEFAULT_MANAGER_MESSAGE];
  const sessionLoadError = chatSessionQuery.error instanceof Error ? chatSessionQuery.error.message : null;
  const sessionBusy = !sessionId || chatSessionQuery.isLoading;

  function renderStatusIcon(status: ToolState | ChatMessage["thinkingState"]) {
    if (status === "running") {
      return <Loader2 size={12} className="spinning" />;
    }
    if (status === "done") {
      return <Check size={12} />;
    }
    if (status === "error") {
      return <AlertTriangle size={12} />;
    }
    return <Sparkles size={12} />;
  }

  return (
    <section className="manager-chat-panel" style={{ maxHeight: "calc(100vh - 140px)" }}>
      <div className="manager-chat-header">
        <div>
          <div className="manager-chat-kicker">Manager AI</div>
          <h3>
            <Sparkles size={16} />
            Blueprint copilot
          </h3>
        </div>
        <span>{busy ? "Responding…" : uploadMutation.isPending ? "Uploading…" : `Thinking ${thinkingEffort}`}</span>
      </div>
      <div className="manager-chat-body">
        {error ? (
          <div className="notice-panel error">
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <AlertTriangle size={14} />
              {error}
            </div>
          </div>
        ) : null}

        <div ref={scrollRef} className="manager-chat-scroll">
          {sessionBusy ? (
            <div className="manager-session-empty">正在加载当前 session…</div>
          ) : sessionLoadError ? (
            <div className="manager-session-empty">当前 session 加载失败：{sessionLoadError}</div>
          ) : (
            displayMessages.map((message) => (
            <div key={message.id} className={`manager-message-row ${message.role}`}>
              {message.role === "manager" ? <div className="manager-message-avatar">M</div> : null}
              <div className={`manager-message-content ${message.role}`}>
                {message.role === "manager" ? <div className="manager-message-role">Manager</div> : null}
                {message.role === "manager" && (message.thinking || message.thinkingState === "running") ? (
                  <details className="manager-thinking-panel" open={message.thinkingState === "running"}>
                    <summary>
                      <span className="manager-thinking-label">
                        {renderStatusIcon(message.thinkingState)}
                        Thinking
                      </span>
                      <ChevronDown size={12} />
                    </summary>
                    <div
                      ref={(element) => {
                        thinkingRefs.current[message.id] = element;
                      }}
                      className="manager-thinking-text"
                    >
                      {(message.thinking || "").split("\n").map((line, index) => (
                        <div key={`${message.id}-thinking-${index}`}>{line}</div>
                      ))}
                    </div>
                  </details>
                ) : null}
                {message.role === "manager" && message.tools?.length ? (
                  <div className="manager-tool-stack">
                    {message.tools.map((tool) => (
                      <div key={tool.id} className={`manager-tool-pill ${tool.status}`}>
                        <span className="manager-tool-label">
                          {renderStatusIcon(tool.status)}
                          {tool.label}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className={`manager-message-bubble ${message.role}`}>
                  <div className="manager-message-text">
                    {message.content}
                    {message.role === "manager" && (message.state === "thinking" || message.state === "streaming") ? (
                      <span className="manager-stream-cursor" />
                    ) : null}
                  </div>
                </div>
              </div>
            </div>
            ))
          )}
        </div>

        {attachments.length ? (
          <div className="attachment-bar">
            {attachments.map((a) => (
              <span key={a.id} className="attachment-pill" onClick={() => removeAttachment(projectId, a.id)}>
                {a.type === "card" ? "📋" : "📎"} {a.label}
                <X size={12} />
              </span>
            ))}
          </div>
        ) : null}

        <div className="manager-composer-shell">
          <div className="manager-composer">
            <input ref={fileInputRef} type="file" hidden onChange={handleFileChange} />
            <button
              className={`manager-upload-button ${uploadMutation.isPending ? "loading" : ""}`}
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || uploadMutation.isPending || sessionBusy || Boolean(sessionLoadError)}
              title="上传文件到后端"
            >
              {uploadMutation.isPending ? <Loader2 size={17} /> : <Paperclip size={17} />}
            </button>
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                syncMentionState(e.target.value, e.target.selectionStart);
              }}
              placeholder="Message Blueprint copilot..."
              onKeyDown={(e) => {
                if (mentionState && mentionOptions.length) {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setMentionIndex((current) => (current + 1) % mentionOptions.length);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setMentionIndex((current) => (current - 1 + mentionOptions.length) % mentionOptions.length);
                    return;
                  }
                  if (e.key === "Enter" || e.key === "Tab") {
                    e.preventDefault();
                    insertMention(mentionOptions[mentionIndex] ?? mentionOptions[0]);
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setMentionState(null);
                    return;
                  }
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              disabled={sessionBusy || Boolean(sessionLoadError)}
              onClick={(e) => syncMentionState(e.currentTarget.value, e.currentTarget.selectionStart)}
              onKeyUp={(e) => syncMentionState(e.currentTarget.value, e.currentTarget.selectionStart)}
            />
            {busy ? (
              <button className="manager-stop-button" onClick={stopGeneration} type="button" title="停止生成">
                <Square size={14} />
              </button>
            ) : (
              <button
                className="manager-send-button"
                onClick={submit}
                disabled={!draft.trim() || sessionBusy || Boolean(sessionLoadError)}
                type="button"
              >
                <Send size={16} />
              </button>
            )}
          </div>
          {mentionState && mentionOptions.length ? (
            <div className="mention-menu">
              {mentionOptions.map((asset, index) => (
                <button
                  key={asset.asset_id}
                  type="button"
                  className={`mention-menu-item ${index === mentionIndex ? "active" : ""}`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    insertMention(asset);
                  }}
                >
                  <span className="mention-menu-title">@{asset.title}</span>
                  <span className="mention-menu-meta">{asset.asset_type} · {asset.asset_id}</span>
                </button>
              ))}
            </div>
          ) : null}
          <div className="manager-composer-meta">
            <label className="manager-effort-pill">
              <span>Thinking effort</span>
              <select
                value={thinkingEffort}
                onChange={(e) => setThinkingEffort(e.target.value as ThinkingEffort)}
                disabled={busy || sessionBusy}
              >
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
            <div className="manager-hint">`Enter` 发送，`Shift + Enter` 换行</div>
          </div>
        </div>
      </div>
    </section>
  );

  function renderProposalControls(proposal: Proposal) {
    const editing = editingProposalId === proposal.proposal_id;
    if (proposal.status !== "proposed") {
      return (
        <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
          提案已
          {proposal.status === "accepted"
            ? "接受"
            : proposal.status === "rejected"
              ? "拒绝"
              : `更新为 ${proposal.status}`}
        </div>
      );
    }
    return (
      <div className="proposal-card" style={{ marginTop: 10 }}>
        <h4>📋 {proposal.title}</h4>
        <div className="impact">{proposal.impact_summary}</div>
        {proposal.consistency_warnings.length ? (
          <div className="risk" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <AlertTriangle size={12} />
            {proposal.consistency_warnings.join(" ")}
          </div>
        ) : null}
        <div className="proposal-actions">
          <button className="btn success" onClick={() => accept(proposal.proposal_id)} disabled={busy}>
            <Check size={14} />
            接受
          </button>
          <button
            className="btn secondary"
            onClick={() => {
              setEditingProposalId(proposal.proposal_id);
              setEditDraft(proposal.summary);
            }}
            disabled={busy}
          >
            <Pencil size={14} />
            修改
          </button>
          <button className="btn danger" onClick={() => reject(proposal.proposal_id)} disabled={busy}>
            <X size={14} />
            拒绝
          </button>
        </div>
        {editing ? (
          <div className="chat-input" style={{ marginTop: 10 }}>
            <textarea
              value={editDraft}
              onChange={(e) => setEditDraft(e.target.value)}
              placeholder="描述你希望如何修改提案"
              style={{ minHeight: 70 }}
            />
            <div className="proposal-actions">
              <button className="btn primary" onClick={() => modify(proposal.proposal_id)} disabled={busy}>
                <Check size={14} />
                提交修改
              </button>
              <button
                className="btn secondary"
                onClick={() => {
                  setEditingProposalId(null);
                  setEditDraft("");
                }}
                disabled={busy}
              >
                <X size={14} />
                取消
              </button>
            </div>
          </div>
        ) : null}
      </div>
    );
  }
}

function getMentionState(text: string, cursor: number): MentionState | null {
  const beforeCursor = text.slice(0, cursor);
  const atIndex = beforeCursor.lastIndexOf("@");
  if (atIndex < 0) {
    return null;
  }
  const prefix = beforeCursor.slice(0, atIndex);
  if (prefix && !/\s$/.test(prefix)) {
    return null;
  }
  const query = beforeCursor.slice(atIndex + 1);
  if (/\s/.test(query)) {
    return null;
  }
  return {
    query,
    start: atIndex,
    end: cursor,
  };
}
