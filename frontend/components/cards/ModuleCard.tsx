"use client";

import { useMemo } from "react";
import {
  CheckCircle2,
  Files,
  Pencil,
  Play,
  RotateCcw,
  Trash2,
  Sparkles,
  Archive,
} from "lucide-react";
import { Card, WorkerCapability } from "@/lib/types";
import { CardStatusBadge } from "./CardStatusBadge";
import { SpecialistAvatar } from "./SpecialistAvatar";
import { FileBag } from "./FileBag";
import { CardPage, EMPTY_CARD_PAGE_BY_ID, useWorkspaceUiStore } from "@/lib/stores/workspace-ui-store";

export function ModuleCard({
  projectId,
  card,
  active,
  onSelect,
  onStartRun,
  onReviewRun,
  onAskManager,
  workerCapabilities = [],
  selectedWorkerType,
  onSelectWorker,
}: {
  projectId: string;
  card: Card;
  active: boolean;
  onSelect: (card: Card) => void;
  onStartRun: (card: Card) => void;
  onReviewRun: (card: Card) => void;
  onAskManager?: (text: string) => void;
  workerCapabilities?: WorkerCapability[];
  selectedWorkerType?: string;
  onSelectWorker?: (card: Card, workerType: string) => void;
}) {
  const cardPages = useWorkspaceUiStore((s) => s.cardPageByProject[projectId] ?? EMPTY_CARD_PAGE_BY_ID);
  const setCardPage = useWorkspaceUiStore((s) => s.setCardPage);
  const storedPage = cardPages[card.card_id];
  const fileCount = card.outputs.filter((o) => o.asset_id).length;

  const isGhost = card.status === "proposed";
  const isRunning = card.status === "running" || card.status === "reviewing";
  const isDormant = card.status === "cancelled" || card.status === "rejected";
  const configuredWorkers = workerCapabilities.filter((item) => item.configured);

  const pages: CardPage[] = isDormant
    ? ["specialist", "result", "detail", "archive"]
    : ["specialist", "result", "detail", "files"];
  const currentPage = pages.includes((storedPage as CardPage | undefined) ?? "specialist")
    ? ((storedPage as CardPage | undefined) ?? (isDormant ? "archive" : "specialist"))
    : (isDormant ? "archive" : "specialist");
  const pageIndex = pages.indexOf(currentPage);

  const slideOffset = useMemo(() => -(pageIndex * 25), [pageIndex]);
  const collapsedSummary = card.progress_note || card.summary || card.why || "等待执行";

  function handleDot(page: CardPage, e: React.MouseEvent) {
    e.stopPropagation();
    setCardPage(projectId, card.card_id, page);
  }

  function sendToManager(text: string, e: React.MouseEvent) {
    e.stopPropagation();
    onAskManager?.(text);
  }

  return (
    <div
      className={`task-specialist-card ${active ? "active" : ""} ${isGhost ? "ghost" : ""} ${isRunning ? "running" : ""} ${isDormant ? "dormant" : ""}`}
      onClick={() => onSelect(card)}
      data-card-id={card.card_id}
    >
      {/* Connection anchors for SVG lines */}
      <div className="card-anchor card-anchor-in" data-anchor={`in-${card.card_id}`} />
      <div className="card-anchor card-anchor-out" data-anchor={`out-${card.card_id}`} />

      {/* ID Badge / File Bag Header */}
      <div className="specialist-badge-header">
        <div className="badge-clip"></div>
        <div className="specialist-badge-identity">
          <SpecialistAvatar name={card.title} status={card.status} />
          <div className="specialist-badge-copy">
            <div className="badge-title" title={card.title}>{card.title}</div>
            <div className="badge-status-row">
              <CardStatusBadge status={card.status} />
              {card.aggregate_status ? <span className="pill badge-pill">{card.aggregate_status}</span> : null}
            </div>
          </div>
        </div>
      </div>

      <div className="specialist-card-collapsed">
        <div className="specialist-card-collapsed-summary">{collapsedSummary}</div>
        <div className="specialist-card-collapsed-meta">
          <span>{card.inputs.length} in</span>
          <span>{card.outputs.length} out</span>
          <span>{fileCount} files</span>
        </div>
      </div>

      <div className="file-bag-container">
        <div className="file-bag-tabs" role="tablist" aria-label={`${card.title} card pages`}>
          <button className={`file-bag-tab ${currentPage === "specialist" ? "active" : ""}`} onClick={(e) => handleDot("specialist", e)}>封面</button>
          <button className={`file-bag-tab ${currentPage === "result" ? "active" : ""}`} onClick={(e) => handleDot("result", e)}>结果</button>
          <button className={`file-bag-tab ${currentPage === "detail" ? "active" : ""}`} onClick={(e) => handleDot("detail", e)}>详情</button>
          <button className={`file-bag-tab ${(currentPage === "files" || currentPage === "archive") ? "active" : ""}`} onClick={(e) => handleDot(isDormant ? "archive" : "files", e)}>
            {isDormant ? <Archive size={11} /> : <Files size={11} />} {isDormant ? "归档袋" : "文件袋"}
          </button>
        </div>

        <div className="file-bag-paper-slot">
          <div className="file-bag-paper-slider file-bag-paper-slider-4" style={{ transform: `translateY(${slideOffset}%)` }}>
            
            {/* ─── Page 1: Specialist (Cover) ─── */}
            <div className="file-bag-page">
              <div className="page-content-scroll">
                <div className="badge-summary">
                  {card.progress_note || card.summary || "等待执行"}
                </div>
                
                <div className="badge-stats-row">
                  <div className="badge-stat"><span>📥</span> {card.inputs.length} inputs</div>
                  <div className="badge-stat"><span>📤</span> {card.outputs.length} outputs</div>
                </div>

                <div className="inline-actions" style={{ marginTop: 12 }}>
                  {onAskManager && (
                    <button className="btn secondary" style={{ fontSize: 10, padding: "4px 8px", flex: 1 }} onClick={(e) => sendToManager(`请解释 ${card.title} 的运行情况和当前状态`, e)}>
                      <Sparkles size={12} /> 解释
                    </button>
                  )}
                  {card.status === "accepted" && onAskManager && (
                    <button className="btn secondary" style={{ fontSize: 10, padding: "4px 8px", flex: 1 }} onClick={(e) => sendToManager(`请帮我修改模块 ${card.title}，我想调整分析参数或目标`, e)}>
                      <Pencil size={12} /> 修改
                    </button>
                  )}
                  {isDormant && onAskManager ? (
                    <button className="btn secondary" style={{ fontSize: 10, padding: "4px 8px", flex: 1 }} onClick={(e) => sendToManager(`请恢复卡片 ${card.title}，必要时同步恢复关联模块，并重新纳入蓝图`, e)}>
                      <RotateCcw size={12} /> 恢复
                    </button>
                  ) : null}
                </div>

                {card.status === "planned" ? (
                  <div className="executor-run-control">
                    <label className="executor-select-label" onClick={(e) => e.stopPropagation()}>
                      <span>执行器</span>
                      <select
                        value={selectedWorkerType ?? configuredWorkers[0]?.worker_type ?? ""}
                        onChange={(e) => onSelectWorker?.(card, e.target.value)}
                        disabled={!configuredWorkers.length}
                      >
                        {configuredWorkers.length ? (
                          configuredWorkers.map((item) => (
                            <option key={item.worker_type} value={item.worker_type}>
                              {item.worker_type}{item.execution_mode === "builtin_pi_agent" ? " · DeepSeek" : ""}
                            </option>
                          ))
                        ) : (
                          <option value="">未配置真实执行器</option>
                        )}
                      </select>
                    </label>
                    <button className="btn primary" style={{ width: "100%" }} onClick={(e) => { e.stopPropagation(); onStartRun(card); }}>
                      <Play size={14} /> 开始执行
                    </button>
                  </div>
                ) : null}
              </div>
            </div>

            {/* ─── Page 2: Result ─── */}
            <div className="file-bag-page">
              <div className="page-content-scroll">
                <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 8, color: "var(--text)" }}>RESULT HIGHLIGHTS</div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                  {card.key_findings.length ? (
                    <ul style={{ margin: 0, paddingLeft: 14 }}>
                      {card.key_findings.map((f, i) => <li key={i} style={{ marginBottom: 4 }}>{f}</li>)}
                    </ul>
                  ) : <div>暂无关键结果</div>}
                </div>
                {card.manager_review ? (
                  <div style={{ fontSize: 11, color: "var(--amber-dark)", marginTop: 8, fontWeight: 500, padding: "6px 8px", background: "var(--amber-bg)", borderRadius: 6 }}>
                    评审: {card.manager_review}
                  </div>
                ) : null}
                
                {card.status === "reviewing" ? (
                  <div className="muted" style={{ marginTop: 12, fontSize: 11 }}>
                    Reviewer 正在自动验收，验收通过后会进入 accepted。
                  </div>
                ) : null}
                {card.status === "needs_review" && card.linked_runs.length ? (
                  <button className="btn success" style={{ width: "100%", marginTop: 12 }} onClick={(e) => { e.stopPropagation(); onReviewRun(card); }}>
                    <CheckCircle2 size={14} /> 人工接受旧结果
                  </button>
                ) : null}
              </div>
            </div>

            {/* ─── Page 3: Detail ─── */}
            <div className="file-bag-page">
              <div className="page-content-scroll">
                <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  <div style={{ marginBottom: 6 }}><strong style={{ color: "var(--text)", display: "block" }}>目的:</strong> {card.why || "—"}</div>
                  <div style={{ marginBottom: 6 }}><strong style={{ color: "var(--text)", display: "block" }}>输入:</strong> {card.inputs.map((i) => i.label).join(", ") || "—"}</div>
                  <div style={{ marginBottom: 6 }}><strong style={{ color: "var(--text)", display: "block" }}>输出:</strong> {card.outputs.map((o) => o.label).join(", ") || "—"}</div>
                  <div><strong style={{ color: "var(--text)", display: "block" }}>下一步:</strong> {card.next_actions.join(", ") || "—"}</div>
                </div>

                <div className="inline-actions" style={{ marginTop: 12, borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
                  {card.status === "failed" && (
                    <button className="btn secondary" style={{ fontSize: 10, padding: "4px 8px", flex: 1 }} onClick={(e) => { e.stopPropagation(); onStartRun(card); }}>
                      <RotateCcw size={12} /> 重新运行
                    </button>
                  )}
                  {isDormant && onAskManager ? (
                    <button className="btn secondary" style={{ fontSize: 10, padding: "4px 8px", flex: 1 }} onClick={(e) => sendToManager(`请恢复卡片 ${card.title}，必要时同步恢复关联模块，并重新纳入蓝图`, e)}>
                      <RotateCcw size={12} /> 恢复
                    </button>
                  ) : null}
                  {onAskManager && card.status !== "cancelled" && card.status !== "rejected" && (
                    <button className="btn secondary" style={{ fontSize: 10, padding: "4px 8px", flex: 1, color: "var(--red-dark)" }} onClick={(e) => sendToManager(`请删除模块 ${card.title}`, e)}>
                      <Trash2 size={12} /> 删除
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* ─── Page 4: Files ─── */}
            <div className="file-bag-page">
              <div className="page-content-scroll">
                <FileBag projectId={projectId} card={card} embedded mode={isDormant ? "archive" : "files"} onAskManager={onAskManager} />
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
}
