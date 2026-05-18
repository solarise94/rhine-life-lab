"use client";

import { Check, X } from "lucide-react";

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
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Run Events</h3>
        <span>{run ? `${run.run_id} · ${run.status}` : "No run selected"}</span>
      </div>
      <div className="panel-body stack">
        {approvals.filter((item) => item.decision === "needs_user_confirmation").length ? (
          <div className="meta-block">
            <h4>Pending Approvals</h4>
            <div className="stack">
              {approvals
                .filter((item) => item.decision === "needs_user_confirmation")
                .map((item) => (
                  <div key={item.request_id} className="chat-message">
                    <strong>{item.risk_level}</strong>
                    <div>{item.target}</div>
                    <div className="muted">{item.reason}</div>
                    <div className="proposal-actions">
                      <button className="btn primary" onClick={() => onApprove(item.request_id)}>
                        <Check size={16} />
                        批准
                      </button>
                      <button className="btn secondary" onClick={() => onReject(item.request_id)}>
                        <X size={16} />
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
                <div>{event.message}</div>
                <div className="muted">{event.created_at}</div>
              </div>
            ))
          ) : (
            <div className="empty-state">当前还没有 run 事件。</div>
          )}
        </div>
      </div>
    </section>
  );
}
