"use client";

import { FileText, Image, Table, FileCode } from "lucide-react";
import { Asset } from "@/lib/types";

const TYPE_ICON_BG: Record<string, { bg: string; border: string; color: string }> = {
  image: { bg: "rgba(59,130,246,0.08)", border: "rgba(59,130,246,0.15)", color: "var(--blue)" },
  table: { bg: "rgba(34,197,94,0.08)", border: "rgba(34,197,94,0.15)", color: "var(--green)" },
  markdown: { bg: "rgba(139,92,246,0.08)", border: "rgba(139,92,246,0.15)", color: "var(--purple)" },
  text: { bg: "rgba(6,182,212,0.08)", border: "rgba(6,182,212,0.15)", color: "var(--cyan)" },
};

const TYPE_ICONS: Record<string, React.ReactNode> = {
  image: <Image size={14} />,
  table: <Table size={14} />,
  markdown: <FileText size={14} />,
  text: <FileCode size={14} />,
};

export function ResultsGrid({
  title,
  items,
  selectedAssetId,
  onSelect,
  onPreview,
}: {
  title: string;
  items: Asset[];
  selectedAssetId?: string;
  onSelect: (asset: Asset) => void;
  onPreview?: (asset: Asset) => void;
}) {
  return (
    <div className="panel">
      <div className="panel-header">
        <h3>{title}</h3>
        <span>{items.length}</span>
      </div>
      <div className="panel-body">
        <div className="deck-container" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
          {items.length ? (
            items.map((item, idx) => {
              const style = TYPE_ICON_BG[item.asset_type] || TYPE_ICON_BG.text;
              return (
                <button
                  type="button"
                  key={item.asset_id}
                  className={`result-item result-button ${selectedAssetId === item.asset_id ? "active" : ""} animate-enter`}
                  style={{ animationDelay: `${idx * 40}ms`, minHeight: 100 }}
                  onClick={() => {
                    onSelect(item);
                    onPreview?.(item);
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div
                      style={{
                        width: 32,
                        height: 32,
                        borderRadius: 10,
                        background: style.bg,
                        border: `1px solid ${style.border}`,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: style.color,
                        flexShrink: 0,
                      }}
                    >
                      {TYPE_ICONS[item.asset_type] || <FileText size={14} />}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.3, color: "var(--text)" }}>
                        {item.title}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{item.asset_type}</div>
                    </div>
                  </div>
                  <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
                    {item.summary}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: "auto" }}>
                    <span className="pill">{item.status}</span>
                    {item.report_selected ? (
                      <span
                        className="pill"
                        style={{
                          color: "var(--green-dark)",
                          borderColor: "var(--green-border)",
                          background: "var(--green-bg)",
                        }}
                      >
                        已入选报告
                      </span>
                    ) : null}
                  </div>
                </button>
              );
            })
          ) : (
            <div className="empty-state" style={{ gridColumn: "1 / -1" }}>
              当前没有结果。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
