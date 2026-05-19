"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { AssetDetail } from "@/lib/types";

export function ResultPreviewPanel({
  detail,
}: {
  detail?: AssetDetail;
}) {
  const [showTech, setShowTech] = useState(false);

  if (!detail) {
    return (
      <section className="panel">
        <div className="panel-header">
          <h3>Result Preview</h3>
          <span>No selection</span>
        </div>
        <div className="panel-body">
          <div className="empty-state">选择一个结果查看 markdown、表格或图片预览。</div>
        </div>
      </section>
    );
  }

  const { asset, preview } = detail;
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>{asset.title}</h3>
        <span>{preview.kind}</span>
      </div>
      <div className="panel-body stack">
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
        {preview.kind === "markdown" || preview.kind === "text" ? (
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
