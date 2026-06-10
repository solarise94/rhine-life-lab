"use client";

import { FileText, Image, Table, FileCode } from "lucide-react";
import { Asset } from "@/lib/types";

const TYPE_ICON_CLASS: Record<string, string> = {
  image: "result-icon-blue",
  table: "result-icon-green",
  markdown: "result-icon-purple",
  text: "result-icon-cyan",
};

const TYPE_ICONS: Record<string, React.ReactNode> = {
  image: <Image size={14} />,
  table: <Table size={14} />,
  markdown: <FileText size={14} />,
  text: <FileCode size={14} />,
};

const ASSET_STATUS_LABELS: Record<string, string> = {
  accepted: "已接受",
  rejected: "已拒绝",
  stale: "已过时",
  superseded: "已替代",
  archived: "已归档",
  missing: "缺失",
  active: "活跃",
  candidate: "候选",
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
        <div className="deck-container">
          {items.length ? (
            items.map((item, idx) => {
              const iconClass = TYPE_ICON_CLASS[item.asset_type] || TYPE_ICON_CLASS.text;
              return (
                <button
                  type="button"
                  key={item.asset_id}
                  className={`result-item result-button ${selectedAssetId === item.asset_id ? "active" : ""} animate-enter`}
                  style={{ animationDelay: `${idx * 40}ms` }}
                  onClick={() => {
                    onSelect(item);
                    onPreview?.(item);
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div className={`result-icon ${iconClass}`}>
                      {TYPE_ICONS[item.asset_type] || <FileText size={14} />}
                    </div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="result-title">{item.title}</div>
                      <div className="result-meta">{item.asset_type}</div>
                    </div>
                  </div>
                  <div className="muted result-summary">{item.summary}</div>
                  <div className="result-tags">
                    <span className="pill">{ASSET_STATUS_LABELS[item.status] ?? item.status}</span>
                    {item.report_selected ? (
                      <span className="pill pill-success">已入选报告</span>
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
