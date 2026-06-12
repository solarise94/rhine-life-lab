"use client";

import { useState } from "react";
import { useInstallCapabilityMutation } from "@/lib/hooks";

interface CapabilityInstallPanelProps {
  projectId: string;
  onInstalled?: (kind: "skill" | "mcp", installedId: string) => void;
}

export function CapabilityInstallPanel({ projectId, onInstalled }: CapabilityInstallPanelProps) {
  const installMutation = useInstallCapabilityMutation(projectId);
  const [kind, setKind] = useState<"skill" | "mcp">("skill");
  const [source, setSource] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [result, setResult] = useState<{
    ok: boolean;
    installed_id: string;
    installed_name: string;
    summary: string;
    warnings: string[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleInstall() {
    setError(null);
    setResult(null);
    if (!source.trim()) {
      setError("请输入安装源路径。");
      return;
    }
    try {
      const res = await installMutation.mutateAsync({
        kind,
        source_type: "local_path",
        source: source.trim(),
        overwrite,
      });
      setResult(res);
      onInstalled?.(kind, res.installed_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "安装失败。");
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>安装能力</h3>
        <span>从本地路径安装</span>
      </div>
      <div className="panel-body stack">
        <p className="muted">
          安装后能力会自动出现在 Skill 库或 MCP 库中，可供 Manager 搜索和挂载。
        </p>
        <div className="settings-form-grid">
          <label className="settings-field">
            <span>类型</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as "skill" | "mcp")}>
              <option value="skill">Skill</option>
              <option value="mcp">MCP</option>
            </select>
          </label>
          <label className="settings-field">
            <span>安装源路径</span>
            <input
              type="text"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="例如：workspace/capabilities/my-skill"
            />
          </label>
        </div>
        <label className="settings-check">
          <input
            type="checkbox"
            checked={overwrite}
            onChange={(e) => setOverwrite(e.target.checked)}
          />
          <span>已存在时覆盖安装</span>
        </label>
        <div className="settings-actions">
          <button
            type="button"
            className="settings-button"
            onClick={handleInstall}
            disabled={installMutation.isPending || !source.trim()}
          >
            {installMutation.isPending ? "安装中…" : "安装"}
          </button>
        </div>
        {error ? <div className="notice-panel error">{error}</div> : null}
        {result ? (
          <div className="notice-panel">
            <div className="settings-kv-list">
              <div>
                <strong>状态</strong>
                <span>{result.ok ? "成功" : "部分成功"}</span>
              </div>
              <div>
                <strong>ID</strong>
                <span>{result.installed_id}</span>
              </div>
              <div>
                <strong>名称</strong>
                <span>{result.installed_name}</span>
              </div>
              <div>
                <strong>说明</strong>
                <span>{result.summary}</span>
              </div>
              {result.warnings.length ? (
                <div>
                  <strong>提示</strong>
                  <span>{result.warnings.join("；")}</span>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
