"use client";

import { GitCommit, ShieldAlert } from "lucide-react";
import Editor from "@monaco-editor/react";
import { PythonRuntime, RRuntime } from "@/lib/types";

export function AdvancedPanels({
  graph,
  gitItems,
  readOnly = false,
  pythonRuntimes = [],
  rRuntimes = [],
  globalPythonRuntime,
  globalRRuntime,
  onSelectGlobalPythonRuntime,
  onSelectGlobalRRuntime,
}: {
  graph: Record<string, unknown> | null;
  gitItems: Array<{ hash: string; date: string; subject: string }>;
  readOnly?: boolean;
  pythonRuntimes?: PythonRuntime[];
  rRuntimes?: RRuntime[];
  globalPythonRuntime?: string;
  globalRRuntime?: string;
  onSelectGlobalPythonRuntime?: (runtime: string) => void;
  onSelectGlobalRRuntime?: (runtime: string) => void;
}) {
  const runtimeLabel = globalPythonRuntime && globalPythonRuntime !== "__system__" ? globalPythonRuntime : "系统默认";
  const rRuntimeLabel = globalRRuntime && globalRRuntime !== "__system__" ? globalRRuntime : "系统默认";
  return (
    <section className="panel">
      <div className="panel-header">
        <h3 style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ShieldAlert size={16} style={{ color: "var(--purple)" }} />
          技术详情
        </h3>
        <span style={{ color: "var(--muted)", fontSize: 12 }}>诊断</span>
      </div>
      <div className="panel-body stack">
        <div className="meta-block">
          <h4>执行器运行时</h4>
          <div className="kv">
            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>全局 Python 运行时</span>
              <select
                value={globalPythonRuntime ?? "__system__"}
                onChange={(event) => onSelectGlobalPythonRuntime?.(event.target.value)}
                disabled={readOnly}
                style={{
                  fontSize: 13,
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid var(--line)",
                  background: "var(--panel)",
                  color: "var(--text)",
                }}
              >
                {pythonRuntimes.map((item) => (
                  <option key={`${item.manager}:${item.name}`} value={item.name}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: "grid", gap: 6 }}>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>全局 R 运行时</span>
              <select
                value={globalRRuntime ?? "__system__"}
                onChange={(event) => onSelectGlobalRRuntime?.(event.target.value)}
                disabled={readOnly}
                style={{
                  fontSize: 13,
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid var(--line)",
                  background: "var(--panel)",
                  color: "var(--text)",
                }}
              >
                {rRuntimes.map((item) => (
                  <option key={`${item.manager}:${item.name}`} value={item.name}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="muted" style={{ fontSize: 12 }}>
              当前默认：Python {runtimeLabel} / R {rRuntimeLabel}。单张 card 仍可在执行前覆盖。
            </div>
          </div>
        </div>
        <div
          className="meta-block"
          style={{ padding: 0, overflow: "hidden", border: "1px solid var(--line)" }}
        >
          <Editor
            height="360px"
            defaultLanguage="json"
            value={JSON.stringify(graph, null, 2)}
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
            Git 历史
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
