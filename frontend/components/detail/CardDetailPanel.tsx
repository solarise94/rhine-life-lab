"use client";

import { Card, ProjectSummary, RunEvent, RunRecord, WorkItem } from "@/lib/types";
import { CardStatusBadge } from "@/components/cards/CardStatusBadge";
import { SpecialistAvatar } from "@/components/cards/SpecialistAvatar";

export function CardDetailPanel({
  card,
  summary,
  workItem,
  run,
  latestEvent,
}: {
  card?: Card;
  summary: ProjectSummary;
  workItem?: WorkItem;
  run?: RunRecord;
  latestEvent?: RunEvent;
}) {
  if (!card) {
    return (
      <section className="panel">
        <div className="panel-body" />
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SpecialistAvatar name={card.title} status={card.status} size={32} />
          <div>
            <h3 style={{ margin: 0, fontSize: 14 }}>{card.title}</h3>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{card.card_type}</div>
          </div>
        </div>
        <CardStatusBadge status={card.status} />
      </div>
      <div className="panel-body meta-grid">
        <div className="meta-block">
          <h4>Summary</h4>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{card.summary}</div>
        </div>
        <div className="meta-block">
          <h4>Execution</h4>
          <div className="kv">
            <div style={{ fontSize: 13 }}>Current status: {card.status}</div>
            <div style={{ fontSize: 13 }}>Latest run: {run?.run_id ?? "—"}</div>
            <div style={{ fontSize: 13 }}>Run state: {run?.status ?? "—"}</div>
            <div style={{ fontSize: 13 }}>Worker: {run?.worker_type ?? "—"}</div>
            {latestEvent ? (
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                Latest event: {latestEvent.message}
              </div>
            ) : null}
          </div>
        </div>
        <div className="meta-block">
          <h4>Why</h4>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{card.why || summary.current_goal}</div>
        </div>
        <div className="meta-block">
          <h4>Work Order</h4>
          <div className="kv">
            <div style={{ fontSize: 13 }}>Can start: {workItem ? (workItem.can_start ? "Yes" : "No") : "—"}</div>
            <div style={{ fontSize: 13 }}>Depends on cards: {workItem?.depends_on_card_ids.join(", ") || "—"}</div>
            {!workItem?.can_start && workItem?.block_reasons.length ? (
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                Block reasons: {workItem.block_reasons.join(", ")}
              </div>
            ) : null}
          </div>
        </div>
        <div className="meta-block">
          <h4>Inputs</h4>
          <div className="kv">
            {card.inputs.length ? (
              card.inputs.map((item) => (
                <div key={`${item.label}-${item.asset_id}`} style={{ fontSize: 13 }}>
                  {item.label}
                </div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 13 }}>No linked inputs</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Outputs</h4>
          <div className="kv">
            {card.outputs.length ? (
              card.outputs.map((item) => (
                <div key={`${item.label}-${item.asset_id}`} style={{ fontSize: 13 }}>
                  {item.label}
                </div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 13 }}>No linked outputs</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Manager Review</h4>
          <div style={{ fontSize: 13, lineHeight: 1.5 }}>{card.manager_review || "Pending manager review."}</div>
        </div>
        <div className="meta-block">
          <h4>Key Findings</h4>
          <div className="kv">
            {card.key_findings.length ? (
              card.key_findings.map((item) => (
                <div key={item} style={{ fontSize: 13 }}>{item}</div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 13 }}>No findings yet</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Next Actions</h4>
          <div className="kv">
            {card.next_actions.length ? (
              card.next_actions.map((item) => (
                <div key={item} style={{ fontSize: 13 }}>{item}</div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 13 }}>No actions</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Executor Context</h4>
          <div className="kv">
            <div style={{ fontSize: 13 }}>
              Profile: {typeof card.executor_context?.executor_profile === "string" ? card.executor_context.executor_profile : "—"}
            </div>
            <div style={{ fontSize: 13 }}>
              Skills: {Array.isArray(card.executor_context?.skills) && card.executor_context.skills.length ? card.executor_context.skills.join(", ") : "—"}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
