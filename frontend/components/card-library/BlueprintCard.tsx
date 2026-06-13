"use client";

import { Layers, Wrench, Radio, Tag, Clock } from "lucide-react";

import { CardBlueprintIndexEntry, CardBlueprintDraftIndexEntry, DraftStatus } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function formatDate(value: string | null | undefined) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", year: "numeric" });
}

const STATUS_LABEL: Record<DraftStatus, string> = {
  draft: "草稿",
  needs_review: "待审查",
  approved: "已通过",
  rejected: "已驳回",
  published: "已发布",
};

const STATUS_STYLE: Record<DraftStatus, { bg: string; color: string }> = {
  draft: { bg: "var(--muted-bg, #f3f4f6)", color: "var(--text-secondary, #6b7280)" },
  needs_review: { bg: "var(--amber-bg, #fef3c7)", color: "var(--amber-dark, #92400e)" },
  approved: { bg: "var(--green-bg, #d1fae5)", color: "var(--green-dark, #065f46)" },
  rejected: { bg: "var(--red-bg, #fee2e2)", color: "var(--red-dark, #991b1b)" },
  published: { bg: "var(--blue-bg, #dbeafe)", color: "var(--blue-dark, #1e40af)" },
};

// ---------------------------------------------------------------------------
// Blueprint Card (grid item)
// ---------------------------------------------------------------------------

export interface BlueprintCardProps {
  entry: CardBlueprintIndexEntry | CardBlueprintDraftIndexEntry;
  isSelected: boolean;
  onSelect: () => void;
  status?: DraftStatus | null;
}

export function BlueprintCard({ entry, isSelected, onSelect, status }: BlueprintCardProps) {
  const indexEntry = entry as CardBlueprintIndexEntry;
  const isDraft = "draft_id" in entry;
  const statusStyle = status ? STATUS_STYLE[status] : null;

  return (
    <button
      type="button"
      className={`card-library-item ${isSelected ? "selected" : ""}`}
      style={{ position: "relative" }}
      onClick={onSelect}
    >
      <div className="card-library-cover">
        <Layers size={28} style={{ color: "var(--muted)" }} />
      </div>
      <div className="card-library-body">
        <strong className="card-library-title">{entry.title}</strong>
        <p className="card-library-summary">{entry.summary || "暂无摘要"}</p>
        {entry.tags.length > 0 && (
          <div className="card-library-tags">
            {entry.tags.slice(0, 3).map((tag) => (
              <span key={tag} className="pill" style={{ fontSize: 11 }}>{tag}</span>
            ))}
            {entry.tags.length > 3 && <span className="pill" style={{ fontSize: 11 }}>+{entry.tags.length - 3}</span>}
          </div>
        )}
        <div className="card-library-meta">
          {entry.runtime_hints.length > 0 && (
            <span style={{ background: "var(--blue-bg)", color: "var(--blue-dark)", padding: "1px 5px", borderRadius: 4, fontSize: 10 }}>
              {entry.runtime_hints.join(", ")}
            </span>
          )}
          {entry.skills.length > 0 && <span title="Skills"><Wrench size={12} /> {entry.skills.length}</span>}
          {entry.mcp_servers.length > 0 && <span title="MCP Servers"><Radio size={12} /> {entry.mcp_servers.length}</span>}
          {"use_count" in entry && indexEntry.use_count > 0 && <span><Tag size={12} /> {indexEntry.use_count}次</span>}
          {"last_used_at" in entry && indexEntry.last_used_at && <span><Clock size={12} /> {formatDate(indexEntry.last_used_at)}</span>}
          {isDraft && (entry as CardBlueprintDraftIndexEntry).created_at && (
            <span><Clock size={12} /> {formatDate((entry as CardBlueprintDraftIndexEntry).created_at)}</span>
          )}
        </div>
      </div>
      {status ? (
        <span
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            fontSize: 10,
            fontWeight: 600,
            padding: "2px 6px",
            borderRadius: 4,
            background: statusStyle?.bg,
            color: statusStyle?.color,
          }}
        >
          {STATUS_LABEL[status]}
        </span>
      ) : null}
    </button>
  );
}
