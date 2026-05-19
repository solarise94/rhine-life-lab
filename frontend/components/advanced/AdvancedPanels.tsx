"use client";

import { GitCommit, GitGraph, FileQuestion, ShieldAlert } from "lucide-react";
import Editor from "@monaco-editor/react";

export function AdvancedPanels({
  graph,
  gitItems,
  proposals,
  activeDocument,
  onSelectDocument,
}: {
  graph: Record<string, unknown> | null;
  gitItems: Array<{ hash: string; date: string; subject: string }>;
  proposals: unknown[];
  activeDocument: "graph" | "proposals";
  onSelectDocument: (document: "graph" | "proposals") => void;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ShieldAlert size={16} style={{ color: "var(--purple)" }} />
          Advanced
        </h3>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>Diagnostics</span>
      </div>
      <div className="panel-body stack">
        <div className="proposal-actions" style={{ marginTop: 0 }}>
          <button
            className={`btn ${activeDocument === "graph" ? "primary" : "secondary"}`}
            onClick={() => onSelectDocument("graph")}
            style={{ fontSize: 12 }}
          >
            <GitGraph size={14} />
            Graph
          </button>
          <button
            className={`btn ${activeDocument === "proposals" ? "primary" : "secondary"}`}
            onClick={() => onSelectDocument("proposals")}
            style={{ fontSize: 12 }}
          >
            <FileQuestion size={14} />
            Proposals
          </button>
        </div>
        <div
          className="meta-block"
          style={{ padding: 0, overflow: "hidden", border: "1px solid var(--line)" }}
        >
          <Editor
            height="360px"
            defaultLanguage="json"
            value={JSON.stringify(activeDocument === "graph" ? graph : proposals, null, 2)}
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 13,
              wordWrap: "on",
              scrollBeyondLastLine: false,
            }}
            theme="light"
          />
        </div>
        <div className="meta-block">
          <h4 style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <GitCommit size={12} />
            Git History
          </h4>
          <div className="stack">
            {gitItems.length ? (
              gitItems.map((item) => (
                <div
                  key={item.hash}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: "var(--panel-2)",
                    fontSize: 12,
                    border: "1px solid var(--line)",
                  }}
                >
                  <code style={{ color: "var(--purple)", fontSize: 11, fontFamily: "monospace" }}>
                    {item.hash.slice(0, 7)}
                  </code>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontWeight: 500, color: "var(--text)" }}>{item.subject}</div>
                    <div className="muted" style={{ fontSize: 11 }}>
                      {item.date}
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="muted" style={{ fontSize: 12 }}>暂无提交记录</div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
