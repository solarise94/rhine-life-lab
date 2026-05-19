"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  FileText,
  FolderGit2,
  Layers3,
  Files,
  Beaker,
  MessageSquareText,
  Plus,
  Trash2,
} from "lucide-react";

import { api } from "@/lib/api";
import { useChatSessions } from "@/lib/hooks";
import { queryKeys } from "@/lib/query-keys";
import { useWorkspaceUiStore } from "@/lib/stores/workspace-ui-store";
import { ChatSessionSummary } from "@/lib/types";

const primary = [
  { href: "tasks", label: "蓝图工作台", icon: Layers3 },
  { href: "results", label: "结果库", icon: BarChart3 },
  { href: "files", label: "文件管理", icon: Files },
  { href: "report", label: "报告", icon: FileText },
];

function sortSessions(items: ChatSessionSummary[]) {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function SideNav({ projectId, current }: { projectId: string; current: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const sessionsQuery = useChatSessions(projectId);
  const currentChatSessionId = useWorkspaceUiStore((s) => s.currentChatSessionIdByProject[projectId]);
  const setCurrentChatSessionId = useWorkspaceUiStore((s) => s.setCurrentChatSessionId);
  const clearAttachments = useWorkspaceUiStore((s) => s.clearAttachments);
  const clearDraftMessage = useWorkspaceUiStore((s) => s.clearDraftMessage);
  const sessions = sessionsQuery.data?.items ?? [];

  const createSessionMutation = useMutation({
    mutationFn: () => api.createChatSession(projectId),
    onSuccess: ({ session }) => {
      queryClient.setQueryData(queryKeys.chatSession(projectId, session.session_id), { session });
      queryClient.setQueryData(
        queryKeys.chatSessions(projectId),
        (previous: { items: ChatSessionSummary[] } | undefined) => ({
          items: sortSessions([
            {
              session_id: session.session_id,
              summary: session.summary,
              created_at: session.created_at,
              updated_at: session.updated_at,
              message_count: session.messages.length,
            },
            ...(previous?.items ?? []).filter((item) => item.session_id !== session.session_id),
          ]),
        }),
      );
      setCurrentChatSessionId(projectId, session.session_id);
      clearAttachments(projectId);
      clearDraftMessage(projectId);
      router.push(`/projects/${projectId}/tasks`);
    },
  });

  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) => api.deleteChatSession(projectId, sessionId),
    onSuccess: (_response, deletedSessionId) => {
      queryClient.setQueryData(queryKeys.chatSessions(projectId), (previous: { items: ChatSessionSummary[] } | undefined) => ({
        items: (previous?.items ?? []).filter((item) => item.session_id !== deletedSessionId),
      }));
      queryClient.removeQueries({ queryKey: queryKeys.chatSession(projectId, deletedSessionId) });
      const remaining = queryClient.getQueryData<{ items: ChatSessionSummary[] }>(queryKeys.chatSessions(projectId))?.items ?? [];
      if (currentChatSessionId === deletedSessionId) {
        if (remaining.length) {
          setCurrentChatSessionId(projectId, remaining[0].session_id);
        } else {
          setCurrentChatSessionId(projectId, null);
          createSessionMutation.mutate();
          return;
        }
      }
      clearAttachments(projectId);
      clearDraftMessage(projectId);
      router.push(`/projects/${projectId}/tasks`);
    },
  });

  useEffect(() => {
    if (sessionsQuery.isLoading || sessionsQuery.isError || createSessionMutation.isPending) {
      return;
    }
    if (!sessions.length) {
      createSessionMutation.mutate();
      return;
    }
    if (!currentChatSessionId || !sessions.some((item) => item.session_id === currentChatSessionId)) {
      setCurrentChatSessionId(projectId, sessions[0].session_id);
    }
  }, [
    createSessionMutation.isPending,
    currentChatSessionId,
    projectId,
    sessions,
    sessionsQuery.isError,
    sessionsQuery.isLoading,
    setCurrentChatSessionId,
  ]);

  function openSession(sessionId: string) {
    setCurrentChatSessionId(projectId, sessionId);
    clearAttachments(projectId);
    clearDraftMessage(projectId);
    router.push(`/projects/${projectId}/tasks`);
  }

  return (
    <aside className="side-nav">
      <div className="nav-brand">
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: 10,
              background: "linear-gradient(135deg, #3b82f6, #22c55e)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
              fontSize: 13,
              flexShrink: 0,
              boxShadow: "0 2px 8px rgba(59,130,246,0.3)",
            }}
          >
            <Beaker size={16} />
          </div>
          <div>
            <h1 style={{ fontSize: 15, fontWeight: 700, margin: "0 0 2px", letterSpacing: "-0.2px" }}>
              Blueprint RE
            </h1>
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>
              生信分析蓝图管理器
            </p>
          </div>
        </div>
      </div>

      <div className="nav-section-label">工作台</div>
      <div className="nav-links">
        {primary.map((item) => {
          const Icon = item.icon;
          const href = `/projects/${projectId}/${item.href}`;
          const isActive = current === item.href;
          return (
            <Link key={item.href} href={href} className={`nav-link ${isActive ? "active" : ""}`}>
              <Icon size={16} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>

      <div className="nav-section-label nav-session-label">
        <span>Sessions</span>
        <button
          type="button"
          className="nav-session-add"
          onClick={() => createSessionMutation.mutate()}
          disabled={createSessionMutation.isPending}
          title="新建 session"
        >
          <Plus size={14} />
        </button>
      </div>
      <div className="nav-session-list">
        {sessions.map((session) => {
          const active = currentChatSessionId === session.session_id;
          return (
            <div key={session.session_id} className={`nav-session-item ${active ? "active" : ""}`}>
              <button type="button" className="nav-session-main" onClick={() => openSession(session.session_id)}>
                <MessageSquareText size={14} />
                <span>{session.summary}</span>
              </button>
              <button
                type="button"
                className="nav-session-delete"
                title="删除 session"
                onClick={(event) => {
                  event.stopPropagation();
                  deleteSessionMutation.mutate(session.session_id);
                }}
                disabled={deleteSessionMutation.isPending}
              >
                <Trash2 size={13} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="nav-section-label">高级</div>
      <div className="nav-secondary">
        <Link
          href={`/projects/${projectId}/advanced`}
          className={`nav-link ${current === "advanced" ? "active" : ""}`}
        >
          <FolderGit2 size={16} />
          <span>技术详情</span>
        </Link>
      </div>

      <div style={{ marginTop: "auto", paddingTop: 20 }}>
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 12,
            background: "var(--green-bg)",
            border: "1px solid var(--green-border)",
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 12,
            color: "var(--green-dark)",
            fontWeight: 500,
          }}
        >
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: "var(--green)",
              boxShadow: "0 0 6px rgba(34,197,94,0.4)",
              flexShrink: 0,
            }}
          />
          Manager AI 在线
        </div>
      </div>
    </aside>
  );
}
