"use client";

import { useMutation } from "@tanstack/react-query";
import { Check, Pencil, Send, X } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import { useModifyProposalMutation } from "@/lib/hooks";
import { Proposal } from "@/lib/types";

interface ChatMessage {
  role: "user" | "manager";
  content: string;
  proposal?: Proposal;
}

export function ManagerChatPanel({
  projectId,
  proposals,
  onRefresh,
}: {
  projectId: string;
  proposals: Proposal[];
  onRefresh: () => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: "manager",
      content: "你通过自然语言描述意图，我会先给 proposal，再由后端校验和应用 patch。",
    },
  ]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingProposalId, setEditingProposalId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const sendChatMutation = useMutation({
    mutationFn: (message: string) => api.sendChat(projectId, message),
  });
  const acceptProposalMutation = useMutation({
    mutationFn: (proposalId: string) => api.acceptProposal(projectId, proposalId),
  });
  const rejectProposalMutation = useMutation({
    mutationFn: (proposalId: string) => api.rejectProposal(projectId, proposalId),
  });
  const modifyProposalMutation = useModifyProposalMutation(projectId);

  async function submit() {
    if (!draft.trim() || busy) {
      return;
    }
    const text = draft.trim();
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const response = await sendChatMutation.mutateAsync(text);
      setMessages((prev) => [
        ...prev,
        {
          role: "manager",
          content: response.message,
          proposal: response.proposal as Proposal | undefined,
        },
      ]);
      await onRefresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Chat failed.");
    } finally {
      setBusy(false);
    }
  }

  async function accept(proposalId: string) {
    setBusy(true);
    setError(null);
    try {
      await acceptProposalMutation.mutateAsync(proposalId);
      await onRefresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Accept failed.");
    } finally {
      setBusy(false);
    }
  }

  async function reject(proposalId: string) {
    setBusy(true);
    setError(null);
    try {
      await rejectProposalMutation.mutateAsync(proposalId);
      await onRefresh();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Reject failed.");
    } finally {
      setBusy(false);
    }
  }

  async function modify(proposalId: string) {
    if (!editDraft.trim()) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await modifyProposalMutation.mutateAsync({ proposalId, message: editDraft.trim() });
      const proposal = response.proposal as Proposal;
      setMessages((prev) => [
        ...prev,
        { role: "user", content: `修改提案：${editDraft.trim()}` },
        { role: "manager", content: proposal.summary, proposal },
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

  function renderProposalControls(proposal: Proposal) {
    const editing = editingProposalId === proposal.proposal_id;
    return (
      <div className="stack">
        <div className="proposal-actions">
          <button className="btn primary" onClick={() => accept(proposal.proposal_id)} disabled={busy}>
            <Check size={16} />
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
            <Pencil size={16} />
            修改
          </button>
          <button className="btn secondary" onClick={() => reject(proposal.proposal_id)} disabled={busy}>
            <X size={16} />
            拒绝
          </button>
        </div>
        {proposal.consistency_warnings.length ? (
          <div className="muted">{proposal.consistency_warnings.join(" ")}</div>
        ) : null}
        {editing ? (
          <div className="chat-input">
            <textarea value={editDraft} onChange={(event) => setEditDraft(event.target.value)} placeholder="描述你希望如何修改提案" />
            <div className="proposal-actions">
              <button className="btn primary" onClick={() => modify(proposal.proposal_id)} disabled={busy}>
                <Check size={16} />
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
                <X size={16} />
                取消
              </button>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Manager AI Chat</h3>
        <span>{busy ? "Syncing" : "Intent-first"}</span>
      </div>
      <div className="panel-body stack">
        {error ? <div className="chat-message">{error}</div> : null}
        <div className="stack">
          {messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className="chat-message">
              <strong>{message.role === "user" ? "User" : "Manager"}</strong>
              <div>{message.content}</div>
              {message.proposal ? renderProposalControls(message.proposal) : null}
            </div>
          ))}
          {proposals.filter((item) => item.status === "proposed").length ? (
            <div className="chat-message">
              <strong>Open Proposals</strong>
              <div className="stack">
                {proposals
                  .filter((item) => item.status === "proposed")
                  .map((proposal) => (
                    <div key={proposal.proposal_id} className="stack">
                      <div>{proposal.title}</div>
                      <div className="muted">{proposal.impact_summary}</div>
                      {renderProposalControls(proposal)}
                    </div>
                  ))}
              </div>
            </div>
          ) : null}
        </div>
        <div className="chat-input">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="例如：帮我重跑差异分析并收紧 padj 阈值到 0.01"
          />
          <button className="btn primary" onClick={submit} disabled={busy}>
            <Send size={16} />
            发送意图
          </button>
        </div>
      </div>
    </section>
  );
}
