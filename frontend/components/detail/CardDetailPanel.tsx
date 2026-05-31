"use client";

import { Card, ProjectSummary, RunEvent, RunRecord, WorkItem } from "@/lib/types";
import { CardStatusBadge } from "@/components/cards/CardStatusBadge";
import { SpecialistAvatar } from "@/components/cards/SpecialistAvatar";
import { latestManagerReview } from "@/lib/card-review";

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
        <div className="panel-body empty-state">
          <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--muted)" }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>选择一张卡片查看详情</div>
          </div>
        </div>
      </section>
    );
  }
  const visibleManagerReview = latestManagerReview(card.manager_review);

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
          <div className="meta-text">{card.summary}</div>
        </div>
        <div className="meta-block">
          <h4>Execution</h4>
          <div className="kv">
            <div className="meta-text">Current status: {card.status}</div>
            <div className="meta-text">Latest run: {run?.run_id ?? "—"}</div>
            <div className="meta-text">Run state: {run?.status ?? "—"}</div>
            <div className="meta-text">Worker: {run?.worker_type ?? "—"}</div>
            {latestEvent ? (
              <div className="meta-text" style={{ lineHeight: 1.5 }}>
                Latest event: {latestEvent.message}
              </div>
            ) : null}
          </div>
        </div>
        <div className="meta-block">
          <h4>Why</h4>
          <div className="meta-text" style={{ lineHeight: 1.5 }}>{card.why || summary.current_goal}</div>
        </div>
        <div className="meta-block">
          <h4>Work Order</h4>
          <div className="kv">
            <div className="meta-text">Can start: {workItem ? (workItem.can_start ? "Yes" : "No") : "—"}</div>
            <div className="meta-text">Depends on cards: {workItem?.depends_on_card_ids.join(", ") || "—"}</div>
            {!workItem?.can_start && workItem?.block_reasons.length ? (
              <div className="meta-text" style={{ lineHeight: 1.5 }}>
                Block reasons: {workItem.block_reasons.join(", ")}
              </div>
            ) : null}
          </div>
        </div>
        {workItem?.dependency_attention_count ? (
          <div className="meta-block attention-block">
            <h4>Dependency Attention</h4>
            <div className="kv">
              {(workItem.dependency_attention ?? []).map((issue) => (
                <div key={issue.issue_id} className={`attention-detail ${issue.severity}`}>
                  <div className="attention-detail-title">
                    <span>{issue.kind}</span>
                    <span>{issue.severity}</span>
                  </div>
                  <div className="meta-text">{issue.message || issue.asset_id || issue.issue_id}</div>
                  {issue.current_asset_id ? (
                    <div className="meta-text muted">Current asset: {issue.current_asset_id}</div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <div className="meta-block">
          <h4>Inputs</h4>
          <div className="kv">
            {card.inputs.length ? (
              card.inputs.map((item) => (
                <div key={`${item.label}-${item.asset_id}`} className="meta-text">
                  {item.label}
                </div>
              ))
            ) : (
              <div className="muted meta-text">No linked inputs</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Outputs</h4>
          <div className="kv">
            {card.outputs.length ? (
              card.outputs.map((item) => (
                <div key={`${item.label}-${item.asset_id}`} className="meta-text">
                  {item.label}
                </div>
              ))
            ) : (
              <div className="muted meta-text">No linked outputs</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Manager Review</h4>
          <div className="meta-text" style={{ lineHeight: 1.5 }}>{visibleManagerReview || "Pending manager review."}</div>
        </div>
        <div className="meta-block">
          <h4>Key Findings</h4>
          <div className="kv">
            {card.key_findings.length ? (
              card.key_findings.map((item) => (
                <div key={item} className="meta-text">{item}</div>
              ))
            ) : (
              <div className="muted meta-text">No findings yet</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Next Actions</h4>
          <div className="kv">
            {card.next_actions.length ? (
              card.next_actions.map((item) => (
                <div key={item} className="meta-text">{item}</div>
              ))
            ) : (
              <div className="muted meta-text">No actions</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>Executor Context</h4>
          <div className="kv">
            <div className="meta-text">
              Profile: {typeof card.executor_context?.executor_profile === "string" ? card.executor_context.executor_profile : "—"}
            </div>
            <div className="meta-text">
              Skills: {Array.isArray(card.executor_context?.skills) && card.executor_context.skills.length ? card.executor_context.skills.join(", ") : "—"}
            </div>
            <div className="meta-text">
              MCP: {Array.isArray(card.executor_context?.mcp_servers) && card.executor_context.mcp_servers.length ? card.executor_context.mcp_servers.join(", ") : "—"}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
