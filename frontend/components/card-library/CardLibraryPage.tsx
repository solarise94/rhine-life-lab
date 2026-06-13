"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Search, X, Trash2, Layers, ArrowLeft, Filter } from "lucide-react";

import { useCardLibrary, useDeleteCardBlueprint, useCardBlueprint } from "@/lib/hooks";
import { BlueprintCard } from "./BlueprintCard";
import { BlueprintDetailPanel } from "./BlueprintDetailPanel";

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
        <BlueprintDetailPanel
          blueprint={detailData?.blueprint ?? null}
          entry={selectedEntry}
          actions={
            <button
              type="button"
              className="btn secondary"
              style={{ color: "var(--red)" }}
              onClick={() => {
                deleteMutation.mutate(selectedEntry.blueprint_id, {
                  onSuccess: () => setSelectedId(null),
                });
              }}
              disabled={deleteMutation.isPending}
            >
              <Trash2 size={14} /> {deleteMutation.isPending ? "删除中…" : "删除"}
            </button>
          }
        />
      )}
    </div>
  );
}
