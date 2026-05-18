"use client";

import { Asset } from "@/lib/types";

export function ResultsGrid({
  title,
  items,
  selectedAssetId,
  onSelect,
}: {
  title: string;
  items: Asset[];
  selectedAssetId?: string;
  onSelect: (asset: Asset) => void;
}) {
  return (
    <div className="panel">
      <div className="panel-header">
        <h3>{title}</h3>
        <span>{items.length}</span>
      </div>
      <div className="panel-body results-grid">
        {items.length ? (
          items.map((item) => (
            <button
              type="button"
              className={`result-item result-button ${selectedAssetId === item.asset_id ? "active" : ""}`}
              key={item.asset_id}
              onClick={() => onSelect(item)}
            >
              <div className="status-row">
                <span className="pill">{item.asset_type}</span>
                <span className="pill">{item.status}</span>
              </div>
              <h4>{item.title}</h4>
              <div className="muted">{item.summary}</div>
              <div className="muted" style={{ marginTop: 8 }}>{item.path}</div>
            </button>
          ))
        ) : (
          <div className="empty-state">当前没有结果。</div>
        )}
      </div>
    </div>
  );
}
