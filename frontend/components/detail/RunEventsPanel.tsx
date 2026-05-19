"use client";

import { useState } from "react";
import { Check, ChevronDown, ChevronUp, X } from "lucide-react";

import { RunEvent, RunRecord, RuntimeApprovalDecision } from "@/lib/types";

export function RunEventsPanel({
  run,
  events,
  approvals,
  onApprove,
  onReject,
}: {
  run?: RunRecord;
  events: RunEvent[];
  approvals: RuntimeApprovalDecision[];
  onApprove: (requestId: string) => Promise<void>;
  onReject: (requestId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(true);
  const pending = approvals.filter((item) => item.decision === "needs_user_confirmation");

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
