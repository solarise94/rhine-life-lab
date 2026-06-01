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
        {workItem?.runtime_dependency_blocker?.status === "failed" ? (
          <div className="meta-block attention-block">
            <h4>Runtime Dependency Failure</h4>
            <div className="kv">
              <div className="attention-detail error">
                <div className="attention-detail-title">
                  <span>{workItem.runtime_dependency_blocker.error_code || "dependency_install_failed"}</span>
                  <span>error</span>
                </div>
                <div className="meta-text" style={{ marginBottom: 6 }}>
                  {workItem.runtime_dependency_blocker.message || "Dependency installation failed."}
                </div>
                {workItem.runtime_dependency_blocker.requested_package ? (
                  <div className="meta-text muted">
                    Failed package: {workItem.runtime_dependency_blocker.requested_package}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.runtime ? (
                  <div className="meta-text muted">
                    Runtime: {workItem.runtime_dependency_blocker.runtime}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.attempted_candidates?.length ? (
                  <div className="meta-text muted">
                    {workItem.runtime_dependency_blocker.ecosystem === "R"
                      ? "Conda name variants tried"
                      : "Package tried"}
                    : {workItem.runtime_dependency_blocker.attempted_candidates.join(", ")}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.fallback_available?.length ? (
                  <div className="meta-text muted">
                    Fallback available: {workItem.runtime_dependency_blocker.fallback_available.join(", ")}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.retry_hint ? (
                  <div className="meta-text muted" style={{ marginTop: 4 }}>
                    Action: {(() => {
                      const hint = workItem.runtime_dependency_blocker.retry_hint;
                      if (hint === "do_not_retry_same_conda_request") return "Open runtime detail / edit package list";
                      if (hint === "manual_preparation_required") return "Mark manually resolved";
                      if (hint === "manual_runtime_preparation_required") return "Open runtime settings / mark manually resolved";
                      if (hint === "choose_fallback") return "Try fallback installer only when policy allows it";
                      if (hint === "retry_allowed_after_runtime_check") return "Retry after checking runtime availability";
                      if (hint === "inspect_stderr") return "View stderr tail / lazy fetch job detail";
                      if (hint === "wait_for_existing_dependency_job") return "Wait for existing dependency job";
                      return hint;
                    })()}
                  </div>
                ) : null}
              </div>
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
