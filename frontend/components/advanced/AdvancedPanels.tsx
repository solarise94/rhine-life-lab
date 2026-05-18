"use client";

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
        <h3>Advanced</h3>
        <span>Read-only diagnostics</span>
      </div>
      <div className="panel-body advanced-grid">
        <div className="advanced-item">
          <div className="proposal-actions" style={{ marginTop: 0 }}>
            <button className={`btn secondary ${activeDocument === "graph" ? "active-chip" : ""}`} onClick={() => onSelectDocument("graph")}>
              Graph
            </button>
            <button className={`btn secondary ${activeDocument === "proposals" ? "active-chip" : ""}`} onClick={() => onSelectDocument("proposals")}>
              Proposals
            </button>
          </div>
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
            theme="vs-dark"
          />
        </div>
        <div className="advanced-item">
          <h4>Git History</h4>
          <div className="stack">
            {gitItems.map((item) => (
              <div key={item.hash} className="chat-message">
                <div>{item.subject}</div>
                <div className="muted">{item.date}</div>
                <div className="muted">{item.hash.slice(0, 12)}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="advanced-item">
          <h4>Inspector Notes</h4>
          <div className="muted">
            当前查看：
            {" "}
            {activeDocument === "graph" ? "Graph IR" : "Proposal Store"}
          </div>
          <div className="muted">Monaco 视图与 Git 历史已拆开，避免把所有调试信息塞进一个大 JSON 面板。</div>
        </div>
      </div>
    </section>
  );
}
