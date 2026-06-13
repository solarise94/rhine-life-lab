"use client";

import { useMemo, useState } from "react";
import { Search, Wrench, Radio, X, Layers, Loader2, Check } from "lucide-react";

import { useCardBlueprint, useCardLibrary, useInstantiateCardBlueprint } from "@/lib/hooks";
import { Asset, CardBlueprintIndexEntry, InstantiateBlueprintRequest, PythonRuntime, RRuntime } from "@/lib/types";

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
// Instantiate Form
// ---------------------------------------------------------------------------

function InstantiateForm({
  entry,
  blueprintId,
  projectId,
  pythonRuntimes,
  rRuntimes,
  assets,
  onClose,
}: {
  entry: CardBlueprintIndexEntry;
  blueprintId: string;
  projectId: string;
  pythonRuntimes: PythonRuntime[];
  rRuntimes: RRuntime[];
  assets: Asset[];
  onClose: () => void;
}) {
  const { data: detailData } = useCardBlueprint(blueprintId);
  const blueprint = detailData?.blueprint ?? null;
  const instantiateMutation = useInstantiateCardBlueprint(projectId);
  const [pythonRuntime, setPythonRuntime] = useState("");
  const [rRuntime, setRRuntime] = useState("");
  const [inputBindings, setInputBindings] = useState<Record<string, string>>({});
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [toast, setToast] = useState<string | null>(null);

  function handleInstantiate() {
    const payload: InstantiateBlueprintRequest = {
      input_bindings: inputBindings,
      python_runtime: pythonRuntime || undefined,
      r_runtime: rRuntime || undefined,
      parameter_values: paramValues,
    };
    instantiateMutation.mutate(
      { blueprintId, payload },
      {
        onSuccess: (result) => {
          if (result.blockers.length > 0) {
            setToast(`阻塞: ${result.blockers.join("; ")}`);
            setTimeout(() => setToast(null), 5000);
          } else {
            setToast("已实例化到项目");
            setTimeout(() => {
              setToast(null);
              onClose();
            }, 1500);
          }
        },
        onError: () => {
          setToast("实例化失败");
          setTimeout(() => setToast(null), 3000);
        },
      },
    );
  }

  return (
    <div className="deck-instantiate">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h4>实例化: {entry.title}</h4>
        <button type="button" className="btn secondary" style={{ padding: "2px 6px" }} onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      {/* Input slot bindings — project asset dropdown */}
      {blueprint && blueprint.inputs_schema.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>输入绑定</label>
          {blueprint.inputs_schema.map((inp) => {
            const validAssets = assets.filter(
              (a) => a.status === "valid" || a.status === "candidate",
            );
            return (
              <div key={inp.slot} className="deck-field">
                <label>{inp.label}{inp.required ? " *" : ""}</label>
                {validAssets.length > 0 ? (
                  <select
                    value={inputBindings[inp.slot] ?? ""}
                    onChange={(e) => setInputBindings((prev) => ({ ...prev, [inp.slot]: e.target.value }))}
                  >
                    <option value="">— 选择资产 —</option>
                    {validAssets.map((a) => (
                      <option key={a.asset_id} value={a.asset_id}>
                        {a.title} ({a.asset_id})
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    placeholder="项目中暂无可用资产"
                    value={inputBindings[inp.slot] ?? ""}
                    onChange={(e) => setInputBindings((prev) => ({ ...prev, [inp.slot]: e.target.value }))}
                    disabled
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Parameter inputs */}
      {blueprint && blueprint.parameters.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>参数</label>
          {blueprint.parameters.map((p) => (
            <div key={p.name} className="deck-field">
              <label>{p.name}{p.required ? " *" : ""}</label>
              <input
                type="text"
                placeholder={p.type}
                value={paramValues[p.name] ?? (p.default != null ? String(p.default) : "")}
                onChange={(e) => setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
              />
            </div>
          ))}
        </div>
      )}

      {/* Runtime selection */}
      {pythonRuntimes.length > 0 && (
        <div className="deck-field">
          <label>Python Runtime</label>
          <select value={pythonRuntime} onChange={(e) => setPythonRuntime(e.target.value)}>
            <option value="">自动选择</option>
            {pythonRuntimes.map((rt) => (
              <option key={`${rt.manager}:${rt.name}`} value={rt.name}>{rt.label}</option>
            ))}
          </select>
        </div>
      )}

      {rRuntimes.length > 0 && (
        <div className="deck-field">
          <label>R Runtime</label>
          <select value={rRuntime} onChange={(e) => setRRuntime(e.target.value)}>
            <option value="">自动选择</option>
            {rRuntimes.map((rt) => (
              <option key={`${rt.manager}:${rt.name}`} value={rt.name}>{rt.label}</option>
            ))}
          </select>
        </div>
      )}

      {toast && (
        <div style={{ fontSize: 12, color: toast.includes("失败") || toast.includes("阻塞") ? "var(--red)" : "var(--green)", fontWeight: 500 }}>
          {toast.includes("失败") || toast.includes("阻塞") ? null : <Check size={12} style={{ display: "inline", verticalAlign: -1 }} />}
          {" "}{toast}
        </div>
      )}

      <button
        type="button"
        className="btn primary"
        style={{ marginTop: 4 }}
        onClick={handleInstantiate}
        disabled={instantiateMutation.isPending}
      >
        {instantiateMutation.isPending ? <Loader2 size={14} className="spin" /> : <Layers size={14} />}
        实例化到项目
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BlueprintDeckPanel
// ---------------------------------------------------------------------------

export function BlueprintDeckPanel({
  projectId,
  pythonRuntimes,
  rRuntimes,
  assets,
}: {
  projectId: string;
  pythonRuntimes?: PythonRuntime[];
  rRuntimes?: RRuntime[];
  assets?: Asset[];
}) {
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

      {selectedEntry && (
        <InstantiateForm
          entry={selectedEntry}
          blueprintId={selectedEntry.blueprint_id}
          projectId={projectId}
          pythonRuntimes={pythonRuntimes ?? []}
          rRuntimes={rRuntimes ?? []}
          assets={assets ?? []}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
