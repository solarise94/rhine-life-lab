"use client";

import { Card, ProjectSummary, RunEvent, RunRecord, WorkItem } from "@/lib/types";
import { CardStatusBadge } from "@/components/cards/CardStatusBadge";
import { SpecialistAvatar } from "@/components/cards/SpecialistAvatar";
import { latestManagerReview } from "@/lib/card-review";

export function CardDetailPanel({
  card,
  summary,
  workItem,
  run,
  latestEvent,
}: {
  card?: Card;
  summary: ProjectSummary;
  workItem?: WorkItem;
  run?: RunRecord;
  latestEvent?: RunEvent;
}) {
  if (!card) {
    return (
      <section className="panel card-detail-panel">
        <div className="panel-body card-detail-panel-body empty-state">
          <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--muted)" }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>选择一张卡片查看详情</div>
          </div>
        </div>
      </section>
    );
  }
  const visibleManagerReview = latestManagerReview(card.manager_review);

  return (
    <section className="panel card-detail-panel">
      <div className="panel-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SpecialistAvatar name={card.title} status={card.status} size={32} />
          <div>
            <h3 style={{ margin: 0, fontSize: 14 }}>{card.title}</h3>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{card.card_type}</div>
          </div>
        </div>
        <CardStatusBadge status={card.status} />
      </div>
      <div className="panel-body card-detail-panel-body meta-grid">
        <div className="meta-block">
          <h4>摘要</h4>
          <div className="meta-text">{card.summary}</div>
        </div>
        <div className="meta-block">
          <h4>执行状态</h4>
          <div className="kv">
            <div className="meta-text">当前状态：{card.status}</div>
            <div className="meta-text">最新运行：{run?.run_id ?? "—"}</div>
            <div className="meta-text">运行状态：{run?.status ?? "—"}</div>
            <div className="meta-text">执行器：{run?.worker_type ?? "—"}</div>
            {latestEvent ? (
              <div className="meta-text" style={{ lineHeight: 1.5 }}>
                最新事件：{latestEvent.message}
              </div>
            ) : null}
          </div>
        </div>
        <div className="meta-block">
          <h4>原因</h4>
          <div className="meta-text" style={{ lineHeight: 1.5 }}>{card.why || summary.current_goal}</div>
        </div>
        <div className="meta-block">
          <h4>工作顺序</h4>
          <div className="kv">
            <div className="meta-text">可启动：{workItem ? (workItem.can_start ? "是" : "否") : "—"}</div>
            <div className="meta-text">依赖卡片：{workItem?.depends_on_card_ids.join(", ") || "—"}</div>
            {!workItem?.can_start && workItem?.block_reasons.length ? (
              <div className="meta-text" style={{ lineHeight: 1.5 }}>
                阻塞原因：{workItem.block_reasons.join(", ")}
              </div>
            ) : null}
          </div>
        </div>
        {workItem?.dependency_attention_count ? (
          <div className="meta-block attention-block">
            <h4>依赖关注</h4>
            <div className="kv">
              {(workItem.dependency_attention ?? []).map((issue) => (
                <div key={issue.issue_id} className={`attention-detail ${issue.severity}`}>
                  <div className="attention-detail-title">
                    <span>{issue.kind}</span>
                    <span>{issue.severity}</span>
                  </div>
                  <div className="meta-text">{issue.message || issue.asset_id || issue.issue_id}</div>
                  {issue.current_asset_id ? (
                    <div className="meta-text muted">当前资产：{issue.current_asset_id}</div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {workItem?.runtime_dependency_blocker?.status === "failed" ? (
          <div className="meta-block attention-block">
            <h4>运行时依赖失败</h4>
            <div className="kv">
              <div className="attention-detail error">
                <div className="attention-detail-title">
                  <span>{workItem.runtime_dependency_blocker.error_code || "dependency_install_failed"}</span>
                  <span>error</span>
                </div>
                <div className="meta-text" style={{ marginBottom: 6 }}>
                  {workItem.runtime_dependency_blocker.message || "依赖安装失败。"}
                </div>
                {workItem.runtime_dependency_blocker.requested_package ? (
                  <div className="meta-text muted">
                    失败包：{workItem.runtime_dependency_blocker.requested_package}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.runtime ? (
                  <div className="meta-text muted">
                    运行时：{workItem.runtime_dependency_blocker.runtime}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.attempted_candidates?.length ? (
                  <div className="meta-text muted">
                    {workItem.runtime_dependency_blocker.ecosystem === "R"
                      ? "已尝试 Conda 名称变体"
                      : "已尝试包名"}
                    ：{workItem.runtime_dependency_blocker.attempted_candidates.join(", ")}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.fallback_available?.length ? (
                  <div className="meta-text muted">
                    可用回退：{workItem.runtime_dependency_blocker.fallback_available.join(", ")}
                  </div>
                ) : null}
                {workItem.runtime_dependency_blocker.retry_hint ? (
                  <div className="meta-text muted" style={{ marginTop: 4 }}>
                    操作建议：{(() => {
                      const hint = workItem.runtime_dependency_blocker.retry_hint;
                      if (hint === "do_not_retry_same_conda_request") return "打开运行时详情 / 编辑包列表";
                      if (hint === "manual_preparation_required") return "标记为已手动解决";
                      if (hint === "manual_runtime_preparation_required") return "打开运行时设置 / 标记为已手动解决";
                      if (hint === "choose_fallback") return "仅在策略允许时尝试回退安装器";
                      if (hint === "retry_allowed_after_runtime_check") return "检查运行时可用性后重试";
                      if (hint === "inspect_stderr") return "查看 stderr 尾部 / 延迟获取任务详情";
                      if (hint === "wait_for_existing_dependency_job") return "等待现有依赖任务完成";
                      return hint;
                    })()}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
        <div className="meta-block">
          <h4>输入</h4>
          <div className="kv">
            {card.inputs.length ? (
              card.inputs.map((item) => (
                <div key={`${item.label}-${item.asset_id}`} className="meta-text">
                  {item.label}
                </div>
              ))
            ) : (
              <div className="muted meta-text">无关联输入</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>输出</h4>
          <div className="kv">
            {card.outputs.length ? (
              card.outputs.map((item) => (
                <div key={`${item.label}-${item.asset_id}`} className="meta-text">
                  {item.label}
                </div>
              ))
            ) : (
              <div className="muted meta-text">无关联输出</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>审核</h4>
          <div className="meta-text" style={{ lineHeight: 1.5 }}>{visibleManagerReview || "等待审核。"}</div>
        </div>
        <div className="meta-block">
          <h4>关键发现</h4>
          <div className="kv">
            {card.key_findings.length ? (
              card.key_findings.map((item) => (
                <div key={item} className="meta-text">{item}</div>
              ))
            ) : (
              <div className="muted meta-text">暂无发现</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>后续动作</h4>
          <div className="kv">
            {card.next_actions.length ? (
              card.next_actions.map((item) => (
                <div key={item} className="meta-text">{item}</div>
              ))
            ) : (
              <div className="muted meta-text">暂无动作</div>
            )}
          </div>
        </div>
        <div className="meta-block">
          <h4>执行器上下文</h4>
          <div className="kv">
            <div className="meta-text">
              配置：{typeof card.executor_context?.executor_profile === "string" ? card.executor_context.executor_profile : "—"}
            </div>
            <div className="meta-text">
              技能：{Array.isArray(card.executor_context?.skills) && card.executor_context.skills.length ? card.executor_context.skills.join(", ") : "—"}
            </div>
            <div className="meta-text">
              MCP：{Array.isArray(card.executor_context?.mcp_servers) && card.executor_context.mcp_servers.length ? card.executor_context.mcp_servers.join(", ") : "—"}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
