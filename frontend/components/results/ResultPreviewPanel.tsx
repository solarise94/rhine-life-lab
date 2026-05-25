"use client";

import { X } from "lucide-react";
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
}: {
  detail?: AssetDetail;
  title?: string;
  mode?: "panel" | "drawer";
  loading?: boolean;
  error?: string;
  onClose?: () => void;
}) {
  const emptyPanelClassName = mode === "drawer" ? "artifact-preview-drawer-panel artifact-preview-drawer-panel-empty" : "panel";

  if (!detail) {
    return (
      <section className={emptyPanelClassName}>
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
  const panelClassName =
    mode === "drawer"
      ? `artifact-preview-drawer-panel artifact-preview-drawer-panel-${preview.kind}`
      : "panel";
  return (
    <section
      className={panelClassName}
      onClick={(event) => {
        if (mode === "drawer") {
          event.stopPropagation();
        }
      }}
    >
      <div className="panel-header">
        <h3>{asset.title}</h3>
        <div className="artifact-preview-header-meta">
          <span>{asset.asset_type}</span>
          {onClose ? (
            <button type="button" className="artifact-preview-close" onClick={onClose} aria-label="关闭预览">
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>
      <div className="panel-body stack">
        <div className="artifact-preview-summary">
          <span>{preview.kind}</span>
          {preview.size_bytes ? <span>{formatPreviewSize(preview.size_bytes)}</span> : null}
        </div>
        {preview.kind === "markdown" && preview.text ? (
          <div className="meta-block">
            <div className="manager-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.text}</ReactMarkdown>
            </div>
          </div>
        ) : null}
        {preview.kind === "text" ? (
          <div className="meta-block">
            <pre className="code-block">{preview.text}</pre>
          </div>
        ) : null}
        {preview.kind === "table" && preview.table ? (
          <div className="meta-block">
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
            <img className="preview-image" src={preview.content_url} alt={asset.title} />
          </div>
        ) : null}
        {preview.kind === "binary" && preview.content_url ? (
          <div className="meta-block">
            <a href={preview.content_url} target="_blank" rel="noreferrer" className="artifact-preview-open-link">
              打开原始文件
            </a>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function formatPreviewSize(sizeBytes: number) {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}
