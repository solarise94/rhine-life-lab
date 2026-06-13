"use client";

import { useMemo, useState } from "react";
import { Search, Wrench, Radio, X, Layers } from "lucide-react";

import { useCardLibrary, useCardBlueprint } from "@/lib/hooks";
import { CardBlueprintIndexEntry } from "@/lib/types";
import { BlueprintDetailPanel } from "./BlueprintDetailPanel";
import { BlueprintDetailModal } from "./BlueprintDetailModal";

// ---------------------------------------------------------------------------
// Deck Item (list row)
// ---------------------------------------------------------------------------

function DeckItem({
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
      className={`deck-item ${isSelected ? "selected" : ""}`}
      onClick={onSelect}
    >
      <span className="deck-item-title">{entry.title}</span>
      <div className="deck-item-meta">
        {entry.runtime_hints.length > 0 && <span>{entry.runtime_hints[0]}</span>}
        {entry.skills.length > 0 && <span><Wrench size={10} /> {entry.skills.length}</span>}
        {entry.mcp_servers.length > 0 && <span><Radio size={10} /> {entry.mcp_servers.length}</span>}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// BlueprintDeckPanel
// ---------------------------------------------------------------------------

export function BlueprintDeckPanel() {
  const { data, isLoading, isError } = useCardLibrary();
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const entries = data?.entries ?? [];
  const selectedEntry = selectedId ? entries.find((e) => e.blueprint_id === selectedId) ?? null : null;

  // Client-side search
  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return entries;
    const q = searchQuery.toLowerCase();
    return entries.filter((e) =>
      e.title.toLowerCase().includes(q) ||
      e.summary.toLowerCase().includes(q) ||
      e.tags.some((t) => t.toLowerCase().includes(q))
    );
  }, [entries, searchQuery]);

  const { data: detailData } = useCardBlueprint(selectedId);

  return (
    <div className="deck-panel">
      <div className="deck-panel-search">
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

      {isLoading && <div style={{ color: "var(--muted)", fontSize: 13, padding: 8 }}>加载牌库…</div>}
      {isError && <div style={{ color: "var(--red)", fontSize: 13, padding: 8 }}>牌库加载失败</div>}
      {!isLoading && !isError && filtered.length === 0 && (
        <div style={{ color: "var(--muted)", fontSize: 13, padding: 8 }}>
          {entries.length === 0 ? "牌库为空。先存入一些牌。" : "没有匹配的牌"}
        </div>
      )}

      {filtered.map((entry) => (
        <DeckItem
          key={entry.blueprint_id}
          entry={entry}
          isSelected={selectedId === entry.blueprint_id}
          onSelect={() => setSelectedId(selectedId === entry.blueprint_id ? null : entry.blueprint_id)}
        />
      ))}

      <BlueprintDetailModal
        open={Boolean(selectedEntry)}
        title={selectedEntry?.title}
        onClose={() => setSelectedId(null)}
      >
        <BlueprintDetailPanel
          className="card-library-detail-modal"
          blueprint={detailData?.blueprint ?? null}
          entry={selectedEntry ?? undefined}
        />
      </BlueprintDetailModal>
    </div>
  );
}
