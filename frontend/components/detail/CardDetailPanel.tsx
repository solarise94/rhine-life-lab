"use client";

import { Card, ProjectSummary, RunEvent, RunRecord, WorkItem, WorkerCapability } from "@/lib/types";
import { CardStatusBadge } from "@/components/cards/CardStatusBadge";
import { SpecialistAvatar } from "@/components/cards/SpecialistAvatar";

export function CardDetailPanel({
  card,
  summary,
  workItem,
  run,
  latestEvent,
  workerCapabilities,
  selectedWorkerType,
  onSelectWorker,
}: {
  card?: Card;
  summary: ProjectSummary;
  workItem?: WorkItem;
  run?: RunRecord;
  latestEvent?: RunEvent;
  workerCapabilities?: WorkerCapability[];
  selectedWorkerType?: string;
  onSelectWorker?: (workerType: string) => void;
}) {
  if (!card) {
    return (
      <section className="panel">
        <div className="panel-header">
          <h3>Detail</h3>
          <span>Selection</span>
        </div>
        <div className="panel-body">
          <div className="empty-state">选择一个 Card 查看输入、输出、评审结论和下一步动作。</div>
        </div>
      </section>
    );
  }

  const configuredWorkers = (workerCapabilities ?? []).filter((item) => item.configured);

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
        <div className="meta-block">
          <h4>Worker Adapters</h4>
          <div className="kv">
            {configuredWorkers.length ? (
              <label style={{ display: "grid", gap: 6, marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>Selected executor</span>
                <select
                  value={selectedWorkerType ?? ""}
                  onChange={(event) => onSelectWorker?.(event.target.value)}
                  style={{
                    fontSize: 13,
                    padding: "8px 10px",
                    borderRadius: 8,
                    border: "1px solid var(--line)",
                    background: "var(--panel)",
                    color: "var(--text)",
                  }}
                >
                  {configuredWorkers.map((item) => (
                    <option key={item.worker_type} value={item.worker_type}>
                      {item.worker_type}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {configuredWorkers.length ? (
              configuredWorkers.map((item) => (
                <div key={item.worker_type} style={{ fontSize: 13, lineHeight: 1.5 }}>
                  {item.worker_type}: {item.execution_mode}
                  {item.launch_template_setting ? ` via ${item.launch_template_setting}` : ""}
                </div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 13 }}>No configured real-worker adapters</div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
