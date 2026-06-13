"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Search, X, Trash2, Layers, Wrench, Radio, Tag, Clock, ArrowLeft, Filter } from "lucide-react";

import { useCardLibrary, useDeleteCardBlueprint, useCardBlueprint } from "@/lib/hooks";
import { CardBlueprintIndexEntry, CardBlueprint } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(value: string | null | undefined) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", year: "numeric" });
}

// ---------------------------------------------------------------------------
// Blueprint Card (grid item)
// ---------------------------------------------------------------------------

function BlueprintCard({
  entry,
  isSelected,
  onSelect,
}: {
  entry: CardBlueprintIndexEntry;
  isSelected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`card-library-item ${isSelected ? "selected" : ""}`}
      onClick={onSelect}
    >
      <div className="card-library-cover">
        <Layers size={28} style={{ color: "var(--muted)" }} />
      </div>
      <div className="card-library-body">
        <strong className="card-library-title">{entry.title}</strong>
        <p className="card-library-summary">{entry.summary || "暂无摘要"}</p>
        {entry.tags.length > 0 && (
          <div className="card-library-tags">
            {entry.tags.slice(0, 3).map((tag) => (
              <span key={tag} className="pill" style={{ fontSize: 11 }}>{tag}</span>
            ))}
            {entry.tags.length > 3 && <span className="pill" style={{ fontSize: 11 }}>+{entry.tags.length - 3}</span>}
          </div>
        )}
        <div className="card-library-meta">
          {entry.runtime_hints.length > 0 && (
            <span style={{ background: "var(--blue-bg)", color: "var(--blue-dark)", padding: "1px 5px", borderRadius: 4, fontSize: 10 }}>
              {entry.runtime_hints.join(", ")}
            </span>
          )}
          {entry.skills.length > 0 && <span title="Skills"><Wrench size={12} /> {entry.skills.length}</span>}
          {entry.mcp_servers.length > 0 && <span title="MCP Servers"><Radio size={12} /> {entry.mcp_servers.length}</span>}
          {entry.use_count > 0 && <span><Tag size={12} /> {entry.use_count}次</span>}
          {entry.last_used_at && <span><Clock size={12} /> {formatDate(entry.last_used_at)}</span>}
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Detail Panel
// ---------------------------------------------------------------------------

function DetailPanel({
  blueprint,
  entry,
  onDelete,
  deleting,
}: {
  blueprint: CardBlueprint | null;
  entry: CardBlueprintIndexEntry;
  onDelete: () => void;
  deleting: boolean;
}) {
  if (!blueprint) {
    return <div className="card-library-detail empty"><p>选择一张牌查看详情</p></div>;
  }

  return (
    <div className="card-library-detail">
      <div className="card-library-detail-header">
        <div>
          <h3 style={{ margin: "0 0 4px" }}>{blueprint.title}</h3>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>{blueprint.summary}</p>
        </div>
        <button
          type="button"
          className="btn secondary"
          style={{ color: "var(--red)", flexShrink: 0 }}
          onClick={onDelete}
          disabled={deleting}
        >
          <Trash2 size={14} /> {deleting ? "删除中…" : "删除"}
        </button>
      </div>

      <div className="card-library-detail-section">
        <h4>标签 & 领域</h4>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {blueprint.domain && <span className="pill" style={{ background: "var(--blue-bg)", color: "var(--blue-dark)" }}>{blueprint.domain}</span>}
          {blueprint.tags.map((tag) => <span key={tag} className="pill">{tag}</span>)}
        </div>
      </div>

      <div className="card-library-detail-section">
        <h4>Skills & MCP</h4>
        {blueprint.skills.length > 0 && (
          <div className="settings-kv-list">
            {blueprint.skills.map((s) => <div key={s}><span><Wrench size={12} /> Skill</span><strong>{s}</strong></div>)}
          </div>
        )}
        {blueprint.mcp_servers.length > 0 && (
          <div className="settings-kv-list">
            {blueprint.mcp_servers.map((s) => <div key={s}><span><Radio size={12} /> MCP</span><strong>{s}</strong></div>)}
          </div>
        )}
        {blueprint.skills.length === 0 && blueprint.mcp_servers.length === 0 && <p style={{ color: "var(--muted)" }}>无</p>}
      </div>

      {blueprint.inputs_schema.length > 0 && (
        <div className="card-library-detail-section">
          <h4>输入</h4>
          <div className="settings-kv-list">
            {blueprint.inputs_schema.map((inp) => (
              <div key={inp.slot}>
                <span>{inp.label} {inp.required ? "*" : ""}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>{inp.accepted_formats.join(", ") || "任意"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {blueprint.outputs_schema.length > 0 && (
        <div className="card-library-detail-section">
          <h4>输出</h4>
          <div className="settings-kv-list">
            {blueprint.outputs_schema.map((out) => (
              <div key={out.role}>
                <span>{out.label}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>{out.artifact_class} · {out.accepted_formats.join(", ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {blueprint.parameters.length > 0 && (
        <div className="card-library-detail-section">
          <h4>参数</h4>
          <div className="settings-kv-list">
            {blueprint.parameters.map((p) => (
              <div key={p.name}>
                <span>{p.name} {p.required ? "*" : ""}</span>
                <span style={{ color: "var(--muted)", fontSize: 12 }}>{p.type}{p.default != null ? ` · 默认: ${String(p.default)}` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {blueprint.instruction_blocks.length > 0 && (
        <div className="card-library-detail-section">
          <h4>指令</h4>
          <ul style={{ margin: 0, paddingLeft: 18, color: "var(--text-secondary)", fontSize: 13 }}>
            {blueprint.instruction_blocks.map((block, i) => <li key={i}>{block}</li>)}
          </ul>
        </div>
      )}

      <div className="card-library-detail-section">
        <h4>来源</h4>
        <div className="settings-kv-list">
          <div><span>创建时间</span><span>{formatDate(blueprint.provenance.created_at) || "未知"}</span></div>
          <div><span>使用次数</span><span>{blueprint.provenance.use_count}</span></div>
          {blueprint.provenance.last_used_at && <div><span>最近使用</span><span>{formatDate(blueprint.provenance.last_used_at)}</span></div>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function CardLibraryPage() {
  const { data, isLoading, isError } = useCardLibrary();
  const deleteMutation = useDeleteCardBlueprint();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [domainFilter, setDomainFilter] = useState("");
  const [runtimeFilter, setRuntimeFilter] = useState("");

  const entries = data?.entries ?? [];
  const selectedEntry = entries.find((e) => e.blueprint_id === selectedId) ?? null;

  // Extract unique domains and runtime hints from entries for filter options
  const allDomains = useMemo(() => {
    const set = new Set<string>();
    for (const e of entries) if (e.domain) set.add(e.domain);
    return [...set].sort();
  }, [entries]);

  const allRuntimeHints = useMemo(() => {
    const set = new Set<string>();
    for (const e of entries) for (const h of e.runtime_hints) if (h) set.add(h);
    return [...set].sort();
  }, [entries]);

  // Client-side search + filter
  const filtered = useMemo(() => {
    let result = entries;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter((e) =>
        e.title.toLowerCase().includes(q) ||
        e.summary.toLowerCase().includes(q) ||
        e.tags.some((t) => t.toLowerCase().includes(q)) ||
        e.domain.toLowerCase().includes(q)
      );
    }
    if (domainFilter) {
      result = result.filter((e) => e.domain === domainFilter);
    }
    if (runtimeFilter) {
      result = result.filter((e) => e.runtime_hints.includes(runtimeFilter));
    }
    return result;
  }, [entries, searchQuery, domainFilter, runtimeFilter]);

  const hasFilters = domainFilter || runtimeFilter;

  const { data: detailData } = useCardBlueprint(selectedId);

  return (
    <div className="card-library-page">
      <div className="card-library-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Link href="/projects" className="btn secondary" style={{ padding: "6px 10px" }}>
            <ArrowLeft size={16} />
          </Link>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>Card Library</h2>
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 12 }}>牌库 — 浏览和管理可复用的分析配置牌</p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div className="search-input-wrap">
            <Search size={14} style={{ color: "var(--muted)", flexShrink: 0 }} />
            <input
              type="text"
              placeholder="搜索牌库…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery ? (
              <button type="button" onClick={() => setSearchQuery("")} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted)" }}>
                <X size={14} />
              </button>
            ) : null}
          </div>
          {allDomains.length > 0 && (
            <select
              value={domainFilter}
              onChange={(e) => setDomainFilter(e.target.value)}
              style={{ padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 6, background: "var(--bg)", fontSize: 13, color: "var(--text)" }}
            >
              <option value="">所有领域</option>
              {allDomains.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          )}
          {allRuntimeHints.length > 0 && (
            <select
              value={runtimeFilter}
              onChange={(e) => setRuntimeFilter(e.target.value)}
              style={{ padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 6, background: "var(--bg)", fontSize: 13, color: "var(--text)" }}
            >
              <option value="">所有 Runtime</option>
              {allRuntimeHints.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          )}
          {hasFilters && (
            <button
              type="button"
              className="btn secondary"
              style={{ fontSize: 12, padding: "4px 8px" }}
              onClick={() => { setDomainFilter(""); setRuntimeFilter(""); }}
            >
              <X size={12} /> 清除筛选
            </button>
          )}
        </div>
      </div>

      <div className="card-library-content">
        {isLoading && <div className="empty-state">加载牌库…</div>}
        {isError && <div className="empty-state" style={{ color: "var(--red)" }}>牌库加载失败</div>}
        {!isLoading && !isError && filtered.length === 0 && (
          <div className="empty-state">
            <Layers size={32} style={{ color: "var(--muted)", marginBottom: 8 }} />
            <p>{entries.length === 0 ? "还没有牌。完成一个分析项目后，可以把稳定的 card 存入牌库。" : "没有匹配的牌"}</p>
          </div>
        )}
        {!isLoading && !isError && filtered.length > 0 && (
          <div className="card-library-grid">
            {filtered.map((entry) => (
              <BlueprintCard
                key={entry.blueprint_id}
                entry={entry}
                isSelected={selectedId === entry.blueprint_id}
                onSelect={() => setSelectedId(selectedId === entry.blueprint_id ? null : entry.blueprint_id)}
              />
            ))}
          </div>
        )}
      </div>

      {selectedEntry && (
        <DetailPanel
          blueprint={detailData?.blueprint ?? null}
          entry={selectedEntry}
          onDelete={() => {
            deleteMutation.mutate(selectedEntry.blueprint_id, {
              onSuccess: () => setSelectedId(null),
            });
          }}
          deleting={deleteMutation.isPending}
        />
      )}
    </div>
  );
}
