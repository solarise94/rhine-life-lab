"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, ExternalLink, FileText, FolderOpen, Send, Terminal, Trash2, RotateCcw } from "lucide-react";
import { Card } from "@/lib/types";
import { api } from "@/lib/api";
import { useWorkspaceUiStore } from "@/lib/stores/workspace-ui-store";

export function FileBag({
  projectId,
  card,
  embedded = false,
  mode = "files",
  onAskManager,
}: {
  projectId: string;
  card: Card;
  embedded?: boolean;
  mode?: "files" | "archive";
  onAskManager?: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [resultsExpanded, setResultsExpanded] = useState(!embedded);
  const [logsExpanded, setLogsExpanded] = useState(false);
  const [showTech, setShowTech] = useState(false);
  const addAttachment = useWorkspaceUiStore((s) => s.addAttachment);

  const results = card.outputs.filter((o) => o.asset_id);
  const logs = card.linked_runs.length > 0 ? [{ id: `log-${card.card_id}`, label: "运行日志" }] : [];
  const isArchive = mode === "archive";
  const actions = [
    { label: "发送给 Manager", icon: Send, action: () => addAttachment(projectId, { type: "card", id: card.card_id, label: card.title }) },
  ];
  const archiveActions = [
    ...(onAskManager
      ? [
          {
            label: "恢复到蓝图",
            icon: RotateCcw,
            action: () => onAskManager(`请恢复卡片 ${card.title}，必要时同步恢复关联模块，并把状态恢复为 planned 或 proposed。`),
          },
        ]
      : []),
    { label: "发送给 Manager", icon: Send, action: () => addAttachment(projectId, { type: "card", id: card.card_id, label: card.title }) },
  ];
  const isOpen = embedded ? true : open;
  const visibleResults = embedded && !resultsExpanded ? results.slice(0, 3) : results;

  if (isArchive) {
    return (
      <div className={`file-bag archive ${embedded ? "embedded" : ""}`}>
        {!embedded ? (
          <div className="file-bag-header" onClick={() => setOpen(!open)}>
            <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Trash2 size={14} />
              归档袋
            </span>
            {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </div>
        ) : null}
        {isOpen ? (
          <div className="file-bag-body">
            <div className="archive-badge">
              <Trash2 size={12} />
              已归档
            </div>
            <div className="archive-summary">{card.manager_review || card.summary || "这张卡片已放入归档袋。"} </div>
            <div className="archive-meta">
              <div>Card ID: {card.card_id}</div>
              <div>Linked runs: {card.linked_runs.length || 0}</div>
              <div>Linked assets: {card.linked_assets.length || 0}</div>
            </div>
            {card.key_findings.length ? (
              <div className="archive-findings">
                {card.key_findings.slice(0, 3).map((finding, index) => (
                  <div key={`${card.card_id}-archive-${index}`}>{finding}</div>
                ))}
              </div>
            ) : null}
            <div className="file-bag-actions" style={{ marginTop: 8 }}>
              {archiveActions.map((a) => (
                <button key={a.label} onClick={a.action}>
                  <a.icon size={12} />
                  {a.label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className={`file-bag ${embedded ? "embedded" : ""}`}>
      {!embedded ? (
        <div className="file-bag-header" onClick={() => setOpen(!open)}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <FolderOpen size={14} />
            文件袋 {results.length > 0 ? `(${results.length})` : ""}
          </span>
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      ) : null}
      {isOpen ? (
        <div className="file-bag-body">
          {results.length ? (
            <div className="file-bag-section">
              <button
                type="button"
                className="file-bag-section-toggle"
                onClick={() => setResultsExpanded((value) => !value)}
              >
                <span className="file-bag-section-label">结果</span>
                {resultsExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
              {visibleResults.map((r) => (
                <div key={r.asset_id ?? r.label} className="file-bag-item">
                  <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <FileText size={13} />
                    {r.label}
                  </span>
                  <div className="file-bag-actions">
                    {r.asset_id ? (
                      <button
                        onClick={() => {
                          window.open(api.getResultAssetContentUrl(projectId, r.asset_id!), "_blank");
                        }}
                      >
                        <ExternalLink size={11} />
                        查看
                      </button>
                    ) : null}
                    <button
                      onClick={() =>
                        addAttachment(projectId, {
                          type: "asset",
                          id: r.asset_id ?? r.label,
                          label: r.label,
                        })
                      }
                    >
                      <Send size={11} />
                      发送
                    </button>
                  </div>
                </div>
              ))}
              {embedded && results.length > 3 ? (
                <button
                  type="button"
                  className="file-bag-more-button"
                  onClick={() => setResultsExpanded((value) => !value)}
                >
                  {resultsExpanded ? "收起文件" : `展开其余 ${results.length - 3} 个文件`}
                </button>
              ) : null}
            </div>
          ) : null}

          {logs.length ? (
            <div className="file-bag-section">
              <button
                type="button"
                className="file-bag-section-toggle"
                onClick={() => setLogsExpanded((value) => !value)}
              >
                <span className="file-bag-section-label">日志</span>
                {logsExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
              {logsExpanded
                ? logs.map((l) => (
                    <div key={l.id} className="file-bag-item">
                      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <Terminal size={13} />
                        {l.label}
                      </span>
                    </div>
                  ))
                : null}
            </div>
          ) : null}

          <div className="file-bag-section">
            <button
              type="button"
              className="file-bag-section-toggle"
              onClick={() => setShowTech(!showTech)}
            >
              <span className="file-bag-section-label">技术详情</span>
              {showTech ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            </button>
            {showTech ? (
              <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                <div>Card ID: {card.card_id}</div>
                <div>Type: {card.card_type}</div>
                <div>Linked runs: {card.linked_runs.join(", ") || "none"}</div>
                <div>Linked assets: {card.linked_assets.join(", ") || "none"}</div>
              </div>
            ) : null}
          </div>

          <div className="file-bag-actions" style={{ marginTop: 8 }}>
            {actions.map((a) => (
              <button key={a.label} onClick={a.action}>
                <a.icon size={12} />
                {a.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
