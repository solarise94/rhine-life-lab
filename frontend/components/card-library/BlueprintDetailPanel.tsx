"use client";

import { Wrench, Radio } from "lucide-react";

import { CardBlueprint, CardBlueprintIndexEntry, CardBlueprintDraftIndexEntry, BlueprintReviewResult } from "@/lib/types";
import { formatDate } from "./BlueprintCard";

// ---------------------------------------------------------------------------
// Detail Panel
// ---------------------------------------------------------------------------

export interface BlueprintDetailPanelProps {
  blueprint: CardBlueprint | null;
  entry?: CardBlueprintIndexEntry | CardBlueprintDraftIndexEntry;
  review?: BlueprintReviewResult | null;
  actions?: React.ReactNode;
  className?: string;
}

export function BlueprintDetailPanel({ blueprint, entry, review, actions, className }: BlueprintDetailPanelProps) {
  if (!blueprint) {
    return <div className={`card-library-detail empty ${className ?? ""}`.trim()}><p>选择一张牌查看详情</p></div>;
  }

  return (
    <div className={`card-library-detail ${className ?? ""}`.trim()}>
      <div className="card-library-detail-header">
        <div>
          <h3 style={{ margin: "0 0 4px" }}>{blueprint.title}</h3>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>{blueprint.summary}</p>
        </div>
        {actions ? <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>{actions}</div> : null}
      </div>

      <div className="card-library-detail-section">
        <h4>标签 & 领域</h4>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {blueprint.domain && <span className="pill" style={{ background: "var(--blue-bg)", color: "var(--blue-dark)" }}>{blueprint.domain}</span>}
          {blueprint.tags.map((tag) => <span key={tag} className="pill">{tag}</span>)}
        </div>
      </div>

      <div className="card-library-detail-section">
        <h4>Skills & MCP</h4>
        {blueprint.skills.length > 0 && (
          <div className="settings-kv-list">
            {blueprint.skills.map((s) => <div key={s}><span><Wrench size={12} /> Skill</span><strong>{s}</strong></div>)}
          </div>
        )}
        {blueprint.mcp_servers.length > 0 && (
          <div className="settings-kv-list">
            {blueprint.mcp_servers.map((s) => <div key={s}><span><Radio size={12} /> MCP</span><strong>{s}</strong></div>)}
          </div>
        )}
        {blueprint.skills.length === 0 && blueprint.mcp_servers.length === 0 && <p style={{ color: "var(--muted)" }}>无</p>}
      </div>

      {blueprint.inputs_schema.length > 0 && (
        <div className="card-library-detail-section">
          <h4>输入</h4>
          <div className="settings-kv-list">
            {blueprint.inputs_schema.map((inp) => (
              <div key={inp.slot}>
                <span>{inp.label} {inp.required ? "*" : ""}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>{inp.accepted_formats.join(", ") || "任意"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {blueprint.outputs_schema.length > 0 && (
        <div className="card-library-detail-section">
          <h4>输出</h4>
          <div className="settings-kv-list">
            {blueprint.outputs_schema.map((out) => (
              <div key={out.role}>
                <span>{out.label}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>{out.artifact_class} · {out.accepted_formats.join(", ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {blueprint.parameters.length > 0 && (
        <div className="card-library-detail-section">
          <h4>参数</h4>
          <div className="settings-kv-list">
            {blueprint.parameters.map((p) => (
              <div key={p.name}>
                <span>{p.name} {p.required ? "*" : ""}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>{p.type}{p.default != null ? ` · 默认: ${String(p.default)}` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {blueprint.instruction_blocks.length > 0 && (
        <div className="card-library-detail-section">
          <h4>指令</h4>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", fontSize: 13 }}>
            {blueprint.instruction_blocks.map((block, i) => <li key={i}>{block}</li>)}
          </ul>
        </div>
      )}

      {review && review.issues.length > 0 && (
        <div className="card-library-detail-section">
          <h4>审查结果</h4>
          <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--muted)" }}>{review.summary}</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {review.issues.map((issue, i) => (
              <div
                key={i}
                style={{
                  padding: 8,
                  borderRadius: 6,
                  fontSize: 12,
                  background:
                    issue.severity === "error"
                      ? "var(--red-bg)"
                      : issue.severity === "warning"
                        ? "var(--amber-bg)"
                        : "var(--blue-bg)",
                  color:
                    issue.severity === "error"
                      ? "var(--red-dark)"
                      : issue.severity === "warning"
                        ? "var(--amber-dark)"
                        : "var(--blue-dark)",
                }}
              >
                <strong style={{ textTransform: "capitalize" }}>{issue.severity}</strong>
                <span style={{ marginLeft: 6, fontWeight: 500 }}>{issue.field}</span>
                <div style={{ marginTop: 4 }}>{issue.message}</div>
                {issue.suggested_value ? (
                  <div style={{ marginTop: 4, opacity: 0.9 }}>建议: {issue.suggested_value}</div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card-library-detail-section">
        <h4>来源</h4>
        <div className="settings-kv-list">
          <div><span>创建时间</span><span>{formatDate(blueprint.provenance.created_at) || "未知"}</span></div>
          {"use_count" in (entry ?? {}) && (
            <div><span>使用次数</span><span>{(entry as CardBlueprintIndexEntry).use_count}</span></div>
          )}
          {blueprint.provenance.last_used_at && <div><span>最近使用</span><span>{formatDate(blueprint.provenance.last_used_at)}</span></div>}
          {"global_blueprint_id" in (entry ?? {}) && (entry as CardBlueprintDraftIndexEntry).global_blueprint_id && (
            <div><span>全局牌 ID</span><span>{(entry as CardBlueprintDraftIndexEntry).global_blueprint_id}</span></div>
          )}
        </div>
      </div>
    </div>
  );
}
