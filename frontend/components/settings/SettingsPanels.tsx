"use client";

import { useEffect, useMemo, useState } from "react";

import {
  useAppSettings,
  useExportDiagnosticsMutation,
  useLibrary,
  useRefreshLibraryMutation,
  useResummarizeLibraryItemMutation,
  useUpdateAppSettingsMutation,
  useUpdateProjectRuntimePreferencesMutation,
} from "@/lib/hooks";
import { DiagnosticExportResponse, ProjectState, PythonRuntime, RRuntime } from "@/lib/types";

type ScriptPreference = "auto" | "prefer_python" | "prefer_r" | "prefer_mixed";

function formatRuntimeLabel(runtime?: string | null) {
  return runtime && runtime !== "__system__" ? runtime : "__system__";
}

function LibrarySection({
  kind,
  title,
  description,
}: {
  kind: "skill" | "mcp";
  title: string;
  description: string;
}) {
  const libraryQuery = useLibrary(kind);
  const refreshMutation = useRefreshLibraryMutation(kind);
  const resummarizeMutation = useResummarizeLibraryItemMutation(kind);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const items = libraryQuery.data?.items ?? [];
  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;

  return (
    <section className="settings-section">
      <div className="settings-section-header">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <div className="settings-actions">
          <button type="button" className="settings-button" onClick={() => refreshMutation.mutate({ force: false })}>
            刷新注册
          </button>
          <button type="button" className="settings-button secondary" onClick={() => refreshMutation.mutate({ force: true })}>
            强制重建
          </button>
        </div>
      </div>
      <div className="settings-library-grid">
        <div className="settings-library-list">
          {libraryQuery.isLoading ? <div className="settings-empty">加载中…</div> : null}
          {!libraryQuery.isLoading && !items.length ? <div className="settings-empty">暂无条目</div> : null}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`settings-library-item ${selected?.id === item.id ? "active" : ""}`}
              onClick={() => setSelectedId(item.id)}
            >
              <strong>{item.name}</strong>
              <span>{item.id}</span>
              {item.tags?.length ? <em>{item.tags.join(" · ")}</em> : null}
            </button>
          ))}
        </div>
        <div className="settings-library-detail">
          {selected ? (
            <>
              <div className="settings-detail-header">
                <div>
                  <h4>{selected.name}</h4>
                  <p>{selected.summary_long ?? selected.summary}</p>
                </div>
                <button
                  type="button"
                  className="settings-button"
                  onClick={() => resummarizeMutation.mutate(selected.id)}
                  disabled={resummarizeMutation.isPending}
                >
                  重新摘要
                </button>
              </div>
              <div className="settings-kv-list">
                <div><strong>ID</strong><span>{selected.id}</span></div>
                <div><strong>启用</strong><span>{selected.enabled ? "是" : "否"}</span></div>
                <div><strong>标签</strong><span>{selected.tags?.join(", ") || "—"}</span></div>
                <div><strong>Use Cases</strong><span>{selected.use_cases?.join(", ") || "—"}</span></div>
                <div><strong>Runtime</strong><span>{selected.runtime_requirements?.join(", ") || selected.supported_runtimes?.join(", ") || "—"}</span></div>
                <div><strong>Launch Hint</strong><span>{selected.launch_hint || "—"}</span></div>
                <div><strong>Source</strong><span>{selected.source_path ?? selected.source ?? "—"}</span></div>
              </div>
            </>
          ) : (
            <div className="settings-empty">选择一个条目查看详情</div>
          )}
        </div>
      </div>
    </section>
  );
}

export function SettingsPanels({
  projectId,
  project,
  pythonRuntimes,
  rRuntimes,
  readOnly = false,
}: {
  projectId: string;
  project: ProjectState;
  pythonRuntimes: PythonRuntime[];
  rRuntimes: RRuntime[];
  readOnly?: boolean;
}) {
  const appSettingsQuery = useAppSettings();
  const updateAppSettingsMutation = useUpdateAppSettingsMutation();
  const updateRuntimeMutation = useUpdateProjectRuntimePreferencesMutation(projectId);
  const exportDiagnosticsMutation = useExportDiagnosticsMutation(projectId);

  const [deepseekKey, setDeepseekKey] = useState("");
  const [tavilyKey, setTavilyKey] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [managerModel, setManagerModel] = useState(appSettingsQuery.data?.deepseek.manager_model ?? "deepseek-v4-pro");
  const [executorModel, setExecutorModel] = useState(appSettingsQuery.data?.deepseek.executor_model ?? "deepseek-v4-flash");
  const [reviewerModel, setReviewerModel] = useState(appSettingsQuery.data?.deepseek.reviewer_model ?? "deepseek-v4-flash");
  const [summarizerModel, setSummarizerModel] = useState(
    appSettingsQuery.data?.deepseek.library_summarizer_model ?? "deepseek-v4-flash",
  );
  const [deepseekApiBaseUrl, setDeepseekApiBaseUrl] = useState(
    appSettingsQuery.data?.deepseek.api_base_url ?? "https://api.deepseek.com/anthropic",
  );
  const [piDeepseekBaseUrl, setPiDeepseekBaseUrl] = useState(
    appSettingsQuery.data?.deepseek.pi_base_url ?? "https://api.deepseek.com",
  );
  const [webSearchEnabled, setWebSearchEnabled] = useState(appSettingsQuery.data?.web_search.enabled ?? false);
  const [tavilyBaseUrl, setTavilyBaseUrl] = useState(appSettingsQuery.data?.web_search.base_url ?? "https://api.tavily.com");
  const [scriptPreference, setScriptPreference] = useState<ScriptPreference>(project.runtime_preferences.script_preference);
  const [pythonRuntime, setPythonRuntime] = useState(formatRuntimeLabel(project.runtime_preferences.python_runtime));
  const [rRuntime, setRRuntime] = useState(formatRuntimeLabel(project.runtime_preferences.r_runtime));
  const [diagnosticInfo, setDiagnosticInfo] = useState<DiagnosticExportResponse | null>(null);

  const runtimeSummary = useMemo(() => {
    const script =
      scriptPreference === "prefer_python"
        ? "偏好 Python"
        : scriptPreference === "prefer_r"
          ? "偏好 R"
          : scriptPreference === "prefer_mixed"
            ? "按任务选择"
            : "让 Manager 询问";
    return `${script} · Python ${pythonRuntime} · R ${rRuntime}`;
  }, [pythonRuntime, rRuntime, scriptPreference]);

  useEffect(() => {
    if (!appSettingsQuery.data) return;
    setManagerModel(appSettingsQuery.data.deepseek.manager_model);
    setExecutorModel(appSettingsQuery.data.deepseek.executor_model);
    setReviewerModel(appSettingsQuery.data.deepseek.reviewer_model);
    setSummarizerModel(appSettingsQuery.data.deepseek.library_summarizer_model);
    setDeepseekApiBaseUrl(appSettingsQuery.data.deepseek.api_base_url);
    setPiDeepseekBaseUrl(appSettingsQuery.data.deepseek.pi_base_url);
    setWebSearchEnabled(appSettingsQuery.data.web_search.enabled);
    setTavilyBaseUrl(appSettingsQuery.data.web_search.base_url);
  }, [appSettingsQuery.data]);

  async function saveApiSettings() {
    setStatus(null);
    try {
      await updateAppSettingsMutation.mutateAsync({
        deepseek_api_key: deepseekKey || null,
        deepseek_api_base_url: deepseekApiBaseUrl,
        pi_deepseek_base_url: piDeepseekBaseUrl,
        manager_model: managerModel,
        executor_model: executorModel,
        reviewer_model: reviewerModel,
        library_summarizer_model: summarizerModel,
        manager_websearch_enabled: webSearchEnabled,
        tavily_api_key: tavilyKey || null,
        tavily_base_url: tavilyBaseUrl,
      });
      setDeepseekKey("");
      setTavilyKey("");
      setStatus("API 设置已保存。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "API 设置保存失败。");
    }
  }

  async function saveRuntimeSettings() {
    setStatus(null);
    try {
      await updateRuntimeMutation.mutateAsync({
        script_preference: scriptPreference,
        python_runtime: pythonRuntime === "__system__" ? null : pythonRuntime,
        r_runtime: rRuntime === "__system__" ? null : rRuntime,
      });
      setStatus("项目运行时偏好已保存。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "运行时偏好保存失败。");
    }
  }

  async function exportDiagnostics() {
    setStatus(null);
    try {
      const response = await exportDiagnosticsMutation.mutateAsync({ maxRuns: 8 });
      setDiagnosticInfo(response);
      setStatus("诊断包已生成。");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "诊断包导出失败。");
    }
  }

  return (
    <div className="stack settings-stack">
      <section className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>Runtime Preferences</h3>
            <p>项目级持久化运行时偏好。Manager 和 card 执行共用这套设置。</p>
          </div>
          <div className="settings-inline-note">{runtimeSummary}</div>
        </div>
        <div className="settings-form-grid">
          <label className="settings-field">
            <span>脚本偏好</span>
            <select value={scriptPreference} onChange={(event) => setScriptPreference(event.target.value as ScriptPreference)}>
              
              <option value="auto">让 Manager 询问</option>
              <option value="prefer_python">偏好 Python</option>
              <option value="prefer_r">偏好 R</option>
              <option value="prefer_mixed">按任务选择</option>
            </select>
          </label>
          <label className="settings-field">
            <span>Python runtime</span>
            <select value={pythonRuntime} onChange={(event) => setPythonRuntime(event.target.value)} disabled={readOnly}>
              {pythonRuntimes.map((item) => (
                <option key={`${item.manager}:${item.name}`} value={item.name}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="settings-field">
            <span>R runtime</span>
            <select value={rRuntime} onChange={(event) => setRRuntime(event.target.value)} disabled={readOnly}>
              {rRuntimes.map((item) => (
                <option key={`${item.manager}:${item.name}`} value={item.name}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" className="settings-button" disabled={readOnly} onClick={saveRuntimeSettings}>
            保存运行时偏好
          </button>
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>API Settings</h3>
            <p>Manager、reviewer、library summarizer 和 Tavily web search 共用的运行时 API 配置。</p>
          </div>
        </div>
        <div className="settings-form-grid">
          <label className="settings-field">
            <span>DeepSeek key</span>
            <input
              type="password"
              value={deepseekKey}
              onChange={(event) => setDeepseekKey(event.target.value)}
              placeholder={appSettingsQuery.data?.deepseek.api_key_configured ? "已配置，留空保持不变" : "输入 DeepSeek API key"}
            />
          </label>
          <label className="settings-field">
            <span>Manager model</span>
            <input value={managerModel} onChange={(event) => setManagerModel(event.target.value)} />
          </label>
          <label className="settings-field">
            <span>Executor model</span>
            <input value={executorModel} onChange={(event) => setExecutorModel(event.target.value)} />
          </label>
          <label className="settings-field">
            <span>Reviewer model</span>
            <input value={reviewerModel} onChange={(event) => setReviewerModel(event.target.value)} />
          </label>
          <label className="settings-field">
            <span>Library summarizer</span>
            <input value={summarizerModel} onChange={(event) => setSummarizerModel(event.target.value)} />
          </label>
          <label className="settings-field">
            <span>DeepSeek API base</span>
            <input value={deepseekApiBaseUrl} onChange={(event) => setDeepseekApiBaseUrl(event.target.value)} />
          </label>
          <label className="settings-field">
            <span>Pi DeepSeek base</span>
            <input value={piDeepseekBaseUrl} onChange={(event) => setPiDeepseekBaseUrl(event.target.value)} />
          </label>
          <label className="settings-field">
            <span>Tavily key</span>
            <input
              type="password"
              value={tavilyKey}
              onChange={(event) => setTavilyKey(event.target.value)}
              placeholder={appSettingsQuery.data?.web_search.api_key_configured ? "已配置，留空保持不变" : "输入 Tavily API key"}
            />
          </label>
          <label className="settings-field">
            <span>Tavily base</span>
            <input value={tavilyBaseUrl} onChange={(event) => setTavilyBaseUrl(event.target.value)} />
          </label>
          <label className="settings-check">
            <input type="checkbox" checked={webSearchEnabled} onChange={(event) => setWebSearchEnabled(event.target.checked)} />
            <span>启用 Tavily web search</span>
          </label>
        </div>
        <div className="settings-actions">
          <button type="button" className="settings-button" onClick={saveApiSettings}>
            保存 API 设置
          </button>
        </div>
        {status ? <div className="settings-status">{status}</div> : null}
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>Diagnostics</h3>
            <p>导出脱敏诊断包，包含会话、最近运行日志、错误摘要和配置概览，方便协作者回传排查。</p>
          </div>
        </div>
        <div className="settings-actions">
          <button type="button" className="settings-button" onClick={exportDiagnostics} disabled={exportDiagnosticsMutation.isPending}>
            {exportDiagnosticsMutation.isPending ? "正在导出…" : "导出诊断包"}
          </button>
          {diagnosticInfo ? (
            <a className="settings-button secondary" href={diagnosticInfo.download_url}>
              下载诊断包
            </a>
          ) : null}
        </div>
        {diagnosticInfo ? (
          <div className="settings-kv-list">
            <div><strong>导出时间</strong><span>{diagnosticInfo.created_at}</span></div>
            <div><strong>包含 runs</strong><span>{diagnosticInfo.run_count}</span></div>
            <div><strong>包含 sessions</strong><span>{diagnosticInfo.session_count}</span></div>
            <div><strong>保存路径</strong><span>{diagnosticInfo.path}</span></div>
          </div>
        ) : null}
      </section>

      <LibrarySection
        kind="skill"
        title="Skill Library"
        description="注册后的技能库。Manager 默认只读取 id 和名称，再把选中的 id 挂到 card 执行配置。"
      />
      <LibrarySection
        kind="mcp"
        title="MCP Library"
        description="注册后的 MCP 能力库。Manager 默认只读取 id 和名称，再由 wrapper 在 run 启动时生成 run-local MCP 配置。"
      />
    </div>
  );
}
