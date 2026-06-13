"use client";

import { forwardRef } from "react";
import { Layers, Wrench, Radio, Tag, Clock, Globe } from "lucide-react";

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

export const BlueprintCard = forwardRef<HTMLButtonElement, BlueprintCardProps>(
  function BlueprintCard({ entry, isSelected, onSelect, status }, ref) {
    const indexEntry = entry as CardBlueprintIndexEntry;
    const isDraft = "draft_id" in entry;
    const statusStyle = status ? STATUS_STYLE[status] : null;

    const visibleTags = entry.tags.slice(0, 3);
    const hiddenTagCount = entry.tags.length - visibleTags.length;

    return (
      <button
        ref={ref}
        type="button"
        className={`card-library-item ${isSelected ? "selected" : ""}`}
        style={{ position: "relative" }}
        onClick={onSelect}
      >
        <div className="card-library-cover">
          <Layers size={24} style={{ color: "var(--muted)" }} />
          {entry.domain ? (
            <span className="card-library-domain">
              <Globe size={10} /> {entry.domain}
            </span>
          ) : null}
        </div>
        <div className="card-library-body">
          <strong className="card-library-title">{entry.title}</strong>
          <p className="card-library-summary">{entry.summary || "暂无摘要"}</p>

          {(entry.skills.length > 0 || entry.mcp_servers.length > 0) && (
            <div className="card-library-capabilities">
              {entry.skills.slice(0, 1).map((s) => (
                <span key={s} className="capability-chip skill" title={`Skill: ${s}`}>
                  <Wrench size={10} /> {s}
                </span>
              ))}
              {entry.skills.length > 1 && (
                <span className="capability-chip more">+{entry.skills.length - 1}</span>
              )}
              {entry.mcp_servers.slice(0, 1).map((s) => (
                <span key={s} className="capability-chip mcp" title={`MCP: ${s}`}>
                  <Radio size={10} /> {s}
                </span>
              ))}
              {entry.mcp_servers.length > 1 && (
                <span className="capability-chip more">+{entry.mcp_servers.length - 1}</span>
              )}
            </div>
          )}

          {entry.tags.length > 0 && (
            <div className="card-library-tags">
              {visibleTags.map((tag) => (
                <span key={tag} className="pill" style={{ fontSize: 10 }}>{tag}</span>
              ))}
              {hiddenTagCount > 0 && <span className="pill" style={{ fontSize: 10 }}>+{hiddenTagCount}</span>}
            </div>
          )}

          <div className="card-library-meta">
            {entry.runtime_hints.length > 0 && (
              <span className="runtime-hint" title={entry.runtime_hints.join(", ")}>
                {entry.runtime_hints.join(", ")}
              </span>
            )}
            {"use_count" in entry && indexEntry.use_count > 0 && <span><Tag size={10} /> {indexEntry.use_count}</span>}
            {"last_used_at" in entry && indexEntry.last_used_at && <span><Clock size={10} /> {formatDate(indexEntry.last_used_at)}</span>}
            {isDraft && (entry as CardBlueprintDraftIndexEntry).created_at && (
              <span><Clock size={10} /> {formatDate((entry as CardBlueprintDraftIndexEntry).created_at)}</span>
            )}
          </div>
        </div>
        {status ? (
          <span
            className="card-library-status"
            style={{
              background: statusStyle?.bg,
              color: statusStyle?.color,
            }}
          >
            {STATUS_LABEL[status]}
          </span>
        ) : null}
      </button>
    );
  },
);
