"use client";

import { ArrowDown, ArrowUp, FileText } from "lucide-react";

import { ReportSection } from "@/lib/types";

export function ReportBuilder({
  sections,
  onMove,
  onExport,
  selectedSectionId,
  onSelect,
}: {
  sections: ReportSection[];
  onMove: (itemId: string, direction: "up" | "down") => Promise<void>;
  onExport: () => Promise<void>;
  selectedSectionId?: string;
  onSelect: (itemId: string) => void;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Report Builder</h3>
        <div className="proposal-actions">
          <span>{sections.length} sections</span>
          <button className="btn secondary" onClick={onExport}>
            <FileText size={16} />
            导出 HTML
          </button>
        </div>
      </div>
      <div className="panel-body report-grid">
        {sections.length ? (
          sections.map((section, index) => (
            <div
              className={`report-item report-button ${selectedSectionId === section.item_id ? "active" : ""}`}
              key={section.item_id}
              onClick={() => onSelect(section.item_id)}
            >
              <div className="status-row">
                <span className="pill">{section.section}</span>
                <div className="proposal-actions">
                  <button
                    className="btn secondary"
                    onClick={(event) => {
                      event.stopPropagation();
                      onMove(section.item_id, "up");
                    }}
                    disabled={index === 0}
                  >
                    <ArrowUp size={16} />
                    上移
                  </button>
                  <button
                    className="btn secondary"
                    onClick={(event) => {
                      event.stopPropagation();
                      onMove(section.item_id, "down");
                    }}
                    disabled={index === sections.length - 1}
                  >
                    <ArrowDown size={16} />
                    下移
                  </button>
                </div>
              </div>
              <h4>{section.title}</h4>
              <div className="muted">{section.summary}</div>
              {section.assets.length ? <div className="muted">Assets: {section.assets.map((asset) => asset.title).join(", ")}</div> : null}
              {section.claims.length ? <div className="muted">Claims: {section.claims.map((claim) => claim.text).join(" | ")}</div> : null}
            </div>
          ))
        ) : (
          <div className="empty-state">还没有报告章节。</div>
        )}
      </div>
    </section>
  );
}
