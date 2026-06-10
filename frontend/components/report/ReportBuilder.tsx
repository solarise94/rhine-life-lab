"use client";
import { ArrowDown, ArrowUp, FileText, Layers } from "lucide-react";
import { ReportExportResponse, ReportSection } from "@/lib/types";

export function ReportBuilder({
  sections,
  onMove,
  onExport,
  exportInfo,
  selectedSectionId,
  onSelect,
}: {
  sections: ReportSection[];
  onMove: (itemId: string, direction: "up" | "down") => Promise<void>;
  onExport: () => Promise<void>;
  exportInfo?: ReportExportResponse | null;
  selectedSectionId?: string;
  onSelect: (itemId: string) => void;
}) {
  const exportPath = exportInfo?.path ?? null;
  const exportUrl = exportInfo?.content_url ?? null;
  const exportReady = Boolean(exportPath && exportUrl);
  return (
    <section className="panel">
      <div className="panel-header">
        <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FileText size={16} style={{ color: "var(--green)" }} />
          报告
        </h3>
        <div className="proposal-actions">
          <span style={{ color: "var(--muted)", fontSize: 12 }}>{sections.length} 个章节</span>
          <button type="button" className="btn success" onClick={onExport}>
            <FileText size={14} />
            导出 HTML
          </button>
          <button
            type="button"
            className="btn secondary"
            disabled={!exportReady}
            onClick={() => {
              if (!exportUrl) return;
              window.open(exportUrl, "_blank", "noopener,noreferrer");
            }}
          >
            打开
          </button>
          <button
            type="button"
            className="btn secondary"
            disabled={!exportReady}
            onClick={() => {
              if (!exportUrl) return;
              const link = document.createElement("a");
              link.href = exportUrl;
              link.download = "report.html";
              document.body.appendChild(link);
              link.click();
              link.remove();
            }}
          >
            下载
          </button>
          <button
            type="button"
            className="btn secondary"
            disabled={!exportPath}
            onClick={async () => {
              if (!exportPath) return;
              await navigator.clipboard.writeText(exportPath);
            }}
          >
            复制路径
          </button>
        </div>
      </div>
      {exportPath ? <div style={{ padding: "0 16px 10px", color: "var(--muted)", fontSize: 12 }}>导出路径：{exportPath}</div> : null}
      <div className="panel-body">
        <div className="deck-container report-deck">
          {sections.length ? (
            sections.map((section, index) => (
              <div
                className={`report-item report-button ${selectedSectionId === section.item_id ? "active" : ""}`}
                key={section.item_id}
                onClick={() => onSelect(section.item_id)}
                style={{ cursor: "pointer" }}
              >
                <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                  <div className="report-icon">
                    <Layers size={14} />
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="report-title">{section.title}</div>
                    <div className="report-meta">{section.section}</div>
                  </div>
                </div>
                <div className="muted report-summary">{section.summary}</div>
                {section.assets.length ? (
                  <div className="report-assets">
                    📎 {section.assets.map((a) => a.title).join(", ")}
                  </div>
                ) : null}
                <div className="inline-actions report-actions">
                  <button
                    className="btn secondary"
                    style={{ fontSize: 11, padding: "4px 8px", minHeight: 26 }}
                    onClick={(event) => {
                      event.stopPropagation();
                      onMove(section.item_id, "up");
                    }}
                    disabled={index === 0}
                  >
                    <ArrowUp size={12} />
                    上移
                  </button>
                  <button
                    className="btn secondary"
                    style={{ fontSize: 11, padding: "4px 8px", minHeight: 26 }}
                    onClick={(event) => {
                      event.stopPropagation();
                      onMove(section.item_id, "down");
                    }}
                    disabled={index === sections.length - 1}
                  >
                    <ArrowDown size={12} />
                    下移
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="empty-state">还没有报告章节。</div>
          )}
        </div>
      </div>
    </section>
  );
}
