"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, Download, Link2, MessageSquareText, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AssetDetail } from "@/lib/types";

export function ResultPreviewPanel({
  detail,
  title = "Result Preview",
  mode = "panel",
  loading = false,
  error,
  onClose,
  onSendToManager,
  onExplain,
}: {
  detail?: AssetDetail;
  title?: string;
  mode?: "panel" | "drawer";
  loading?: boolean;
  error?: string;
  onClose?: () => void;
  onSendToManager?: (detail: AssetDetail) => void;
  onExplain?: (detail: AssetDetail) => void;
}) {
  const [showTech, setShowTech] = useState(false);

  useEffect(() => {
    setShowTech(false);
  }, [detail?.asset.asset_id]);

  if (!detail) {
    return (
      <section className={mode === "drawer" ? "artifact-preview-drawer-panel" : "panel"}>
        <div className="panel-header">
          <h3>{title}</h3>
          <div className="artifact-preview-header-meta">
            <span>No selection</span>
            {onClose ? (
              <button type="button" className="artifact-preview-close" onClick={onClose} aria-label="关闭预览">
                <X size={14} />
              </button>
            ) : null}
          </div>
        </div>
        <div className="panel-body">
          <div className="empty-state">
            {loading ? "正在加载预览…" : error ? `预览加载失败：${error}` : "选择一个结果查看 markdown、表格或图片预览。"}
          </div>
        </div>
      </section>
    );
  }

  const { asset, preview } = detail;
  return (
    <section className={mode === "drawer" ? "artifact-preview-drawer-panel" : "panel"}>
      <div className="panel-header">
        <h3>{asset.title}</h3>
        <div className="artifact-preview-header-meta">
          <span>{preview.kind}</span>
          {onClose ? (
            <button type="button" className="artifact-preview-close" onClick={onClose} aria-label="关闭预览">
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>
      <div className="panel-body stack">
        <div className="proposal-actions" style={{ marginTop: 0 }}>
          {onSendToManager ? (
            <button type="button" className="btn secondary" onClick={() => onSendToManager(detail)}>
              <Link2 size={14} />
              发送给 Manager
            </button>
          ) : null}
          {onExplain ? (
            <button type="button" className="btn secondary" onClick={() => onExplain(detail)}>
              <MessageSquareText size={14} />
              解释这个结果
            </button>
          ) : null}
          {preview.content_url ? (
            <a href={preview.content_url} target="_blank" rel="noreferrer" className="btn secondary">
              <Download size={14} />
              下载原文件
            </a>
          ) : null}
        </div>
        <div
          className="meta-block"
          style={{ cursor: "pointer" }}
          onClick={() => setShowTech(!showTech)}
        >
          <h4 style={{ display: "flex", alignItems: "center", gap: 6, margin: 0 }}>
            {showTech ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            技术详情
          </h4>
        </div>
        {showTech ? (
          <div className="meta-block">
            <div className="kv" style={{ fontSize: 12, color: "var(--muted)" }}>
              <div>ID: {asset.asset_id}</div>
              <div>Type: {asset.asset_type}</div>
              <div>Status: {asset.status}</div>
              <div>Path: {asset.path}</div>
              <div>Size: {preview.size_bytes ?? 0} bytes</div>
            </div>
          </div>
        ) : null}
        {preview.kind === "markdown" && preview.text ? (
          <div className="meta-block">
            <h4>Markdown Preview</h4>
            <div className="manager-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.text}</ReactMarkdown>
            </div>
          </div>
        ) : null}
        {preview.kind === "text" ? (
          <div className="meta-block">
            <h4>Text Preview</h4>
            <pre className="code-block">{preview.text}</pre>
          </div>
        ) : null}
        {preview.kind === "table" && preview.table ? (
          <div className="meta-block">
            <h4>Table Preview</h4>
            <div className="table-preview">
              <table>
                <thead>
                  <tr>
                    {preview.table.columns.map((column) => (
                      <th key={column}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.table.rows.map((row, rowIndex) => (
                    <tr key={`${asset.asset_id}-${rowIndex}`}>
                      {row.map((cell, cellIndex) => (
                        <td key={`${asset.asset_id}-${rowIndex}-${cellIndex}`}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
        {preview.kind === "image" && preview.content_url ? (
          <div className="meta-block">
            <h4>Image Preview</h4>
            <img className="preview-image" src={preview.content_url} alt={asset.title} />
          </div>
        ) : null}
        {preview.kind === "binary" && preview.content_url ? (
          <div className="meta-block">
            <h4>Binary Asset</h4>
            <a href={preview.content_url} target="_blank" rel="noreferrer" className="btn secondary">
              打开原始文件
            </a>
          </div>
        ) : null}
      </div>
    </section>
  );
}
