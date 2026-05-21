"use client";

import { useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronUp, RotateCcw, Square, Trash2, X } from "lucide-react";

import { Card, RunEvent, RunRecord, RuntimeApprovalDecision } from "@/lib/types";

export function RunEventsPanel({
  card,
  run,
  events,
  approvals,
  onApprove,
  onReject,
  onCancelRun,
  onCleanupRun,
  onResetCard,
  onRerunCard,
  actionPending = false,
}: {
  card?: Card;
  run?: RunRecord;
  events: RunEvent[];
  approvals: RuntimeApprovalDecision[];
  onApprove: (requestId: string) => Promise<void>;
  onReject: (requestId: string) => Promise<void>;
  onCancelRun?: () => Promise<void>;
  onCleanupRun?: () => Promise<void>;
  onResetCard?: () => Promise<void>;
  onRerunCard?: () => Promise<void>;
  actionPending?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const pending = approvals.filter((item) => item.decision === "needs_user_confirmation");
  const progressEvents = events.filter((item) => item.event_type === "executor_progress");
  const issueEvents = events.filter((item) => item.event_type === "executor_issue" || item.event_type === "run_blocked_on_manager");
  const finalReport = [...events].reverse().find((item) => item.event_type === "executor_final_report");
  const finalPayload = finalReport?.payload;

  return (
    <section className="panel">
      <div className="panel-header" style={{ cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <h3>Run Events</h3>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span>{run ? `${run.status}` : "No run"}</span>
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </div>
      {open ? (
        <div className="panel-body stack">
          {run ? (
            <div className="meta-block">
              <h4>Run Control</h4>
              <div className="proposal-actions">
                {["queued", "needs_approval", "running", "reviewing"].includes(run.status) && onCancelRun ? (
                  <button className="btn secondary" disabled={actionPending} onClick={() => onCancelRun()}>
                    <Square size={14} />
                    Cancel
                  </button>
                ) : null}
                {["success", "failed", "cancelled", "reviewed"].includes(run.status) && run.cleanup_status !== "completed" && onCleanupRun ? (
                  <button className="btn secondary" disabled={actionPending} onClick={() => onCleanupRun()}>
                    <Trash2 size={14} />
                    Cleanup
                  </button>
                ) : null}
                {card && ["failed", "needs_review", "rejected", "cancelled"].includes(card.status) && onResetCard ? (
                  <button className="btn secondary" disabled={actionPending} onClick={() => onResetCard()}>
                    <RotateCcw size={14} />
                    Reset Card
                  </button>
                ) : null}
                {card && !["running", "reviewing", "proposed", "superseded", "stale"].includes(card.status) && onRerunCard ? (
                  <button className="btn primary" disabled={actionPending} onClick={() => onRerunCard()}>
                    <RotateCcw size={14} />
                    Rerun
                  </button>
                ) : null}
              </div>
              {run.needs_manager_attention ? (
                <div style={{ marginTop: 8, fontSize: 12, color: "var(--amber-dark)" }}>
                  This run is waiting for manager attention before it can be interpreted safely.
                </div>
              ) : null}
            </div>
          ) : null}
          {pending.length ? (
            <div className="meta-block" style={{ borderColor: "rgba(245, 184, 92, 0.3)" }}>
              <h4 style={{ color: "var(--amber)" }}>待批准</h4>
              <div className="stack">
                {pending.map((item) => (
                  <div key={item.request_id} className="chat-message">
                    <strong>{item.risk_level}</strong>
                    <div>{item.target}</div>
                    <div className="muted">{item.reason}</div>
                    <div className="proposal-actions">
                      <button className="btn primary" onClick={() => onApprove(item.request_id)}>
                        <Check size={14} />
                        批准
                      </button>
                      <button className="btn secondary" onClick={() => onReject(item.request_id)}>
                        <X size={14} />
                        拒绝
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {progressEvents.length ? (
            <div className="meta-block">
              <h4>Structured Progress</h4>
              <div className="stack">
                {progressEvents.slice(-3).map((event) => (
                  <div key={event.event_id} className="chat-message">
                    <strong>{String(event.payload?.stage ?? "progress")}</strong>
                    <div style={{ fontSize: 13 }}>{event.message}</div>
                    {typeof event.payload?.progress === "number" ? (
                      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                        {event.payload.progress}% complete
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {issueEvents.length ? (
            <div className="meta-block" style={{ borderColor: "rgba(245, 184, 92, 0.3)" }}>
              <h4 style={{ color: "var(--amber-dark)" }}>Executor Issues</h4>
              <div className="stack">
                {issueEvents.map((event) => (
                  <div key={event.event_id} className="chat-message">
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <AlertTriangle size={14} />
                      <strong>{String(event.payload?.severity ?? "issue")}</strong>
                    </div>
                    <div style={{ fontSize: 13 }}>{event.message}</div>
                    {Array.isArray(event.payload?.suggested_actions) && event.payload.suggested_actions.length ? (
                      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                        Suggested: {event.payload.suggested_actions.join(", ")}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {finalPayload ? (
            <div className="meta-block">
              <h4>Final Report</h4>
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                <div>{String(finalPayload.summary ?? finalReport?.message ?? "Final report received.")}</div>
                {Array.isArray(finalPayload.key_findings) && finalPayload.key_findings.length ? (
                  <div className="kv" style={{ marginTop: 8 }}>
                    {finalPayload.key_findings.map((item) => (
                      <div key={String(item)} style={{ fontSize: 13 }}>{String(item)}</div>
                    ))}
                  </div>
                ) : null}
                {Array.isArray(finalPayload.warnings) && finalPayload.warnings.length ? (
                  <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
                    Warnings: {finalPayload.warnings.map((item) => String(item)).join(", ")}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          <div className="stack">
            {events.length ? (
              events.map((event) => (
                <div key={event.event_id} className="chat-message">
                  <strong>{event.event_type}</strong>
                  <div style={{ fontSize: 13 }}>{event.message}</div>
                  <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                    {event.created_at}
                  </div>
                </div>
              ))
            ) : (
              <div className="empty-state">当前还没有 run 事件。</div>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
