"use client";

import { useEffect, useMemo, useState } from "react";

import {
  useAppSettings,
  useExecutorProfiles,
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

function ExecutorProfilesSection() {
  const profilesQuery = useExecutorProfiles();
  const profiles = profilesQuery.data?.profiles ?? [];
  const matrix = profilesQuery.data?.support_matrix;

  return (
    <section className="settings-section">
      <div className="settings-section-header">
        <div>
          <h3>Executor Profiles</h3>
          <p>管理执行器配置。Codex 和 Claude Code 仅支持本机 CLI 登录态；项目 API 注入目前只开放给 OpenCode 和 Pi。</p>
        </div>
      </div>
      <div className="settings-kv-list">
        {profiles.map((p) => {
          const cliOk = matrix?.command_configured?.[p.worker_type] ?? false;
          const unsupported = ["codex", "claude_code"].includes(p.worker_type) && p.auth_mode === "project_api";
          return (
            <div key={p.profile_id}>
              <strong>{p.display_name}</strong>
              <span>
                {p.worker_type} · {p.auth_mode}
                {p.api_protocol ? ` · ${p.api_protocol}` : ""}
                {unsupported ? " · (未支持)" : ""}
                {cliOk ? " · CLI ✓" : " · CLI ✗"}
                {p.enabled ? "" : " · 禁用"}
              </span>
            </div>
          );
        })}
        {profiles.length === 0 ? (
          <div><strong>No profiles</strong><span>使用默认配置</span></div>
        ) : null}
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
  const [openaiKey, setOpenaiKey] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [tavilyKey, setTavilyKey] = useState("");
  const [clearDeepseekKey, setClearDeepseekKey] = useState(false);
  const [clearOpenaiKey, setClearOpenaiKey] = useState(false);
  const [clearAnthropicKey, setClearAnthropicKey] = useState(false);
  const [clearTavilyKey, setClearTavilyKey] = useState(false);
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
  const [openaiApiBaseUrl, setOpenaiApiBaseUrl] = useState(appSettingsQuery.data?.openai.api_base_url ?? "https://api.openai.com/v1");
  const [anthropicApiBaseUrl, setAnthropicApiBaseUrl] = useState(
    appSettingsQuery.data?.anthropic.api_base_url ?? "https://api.anthropic.com",
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
    setOpenaiApiBaseUrl(appSettingsQuery.data.openai.api_base_url);
    setAnthropicApiBaseUrl(appSettingsQuery.data.anthropic.api_base_url);
    setWebSearchEnabled(appSettingsQuery.data.web_search.enabled);
    setTavilyBaseUrl(appSettingsQuery.data.web_search.base_url);
  }, [appSettingsQuery.data]);

  async function saveApiSettings() {
    setStatus(null);
    try {
      await updateAppSettingsMutation.mutateAsync({
        deepseek_api_key: deepseekKey || null,
        clear_deepseek_api_key: clearDeepseekKey,
        deepseek_api_base_url: deepseekApiBaseUrl,
        pi_deepseek_base_url: piDeepseekBaseUrl,
        manager_model: managerModel,
        executor_model: executorModel,
        reviewer_model: reviewerModel,
        library_summarizer_model: summarizerModel,
        manager_websearch_enabled: webSearchEnabled,
        tavily_api_key: tavilyKey || null,
        clear_tavily_api_key: clearTavilyKey,
        tavily_base_url: tavilyBaseUrl,
        openai_api_key: openaiKey || null,
        clear_openai_api_key: clearOpenaiKey,
        openai_api_base_url: openaiApiBaseUrl,
        anthropic_api_key: anthropicKey || null,
        clear_anthropic_api_key: clearAnthropicKey,
        anthropic_api_base_url: anthropicApiBaseUrl,
      });
      setDeepseekKey("");
      setOpenaiKey("");
      setAnthropicKey("");
      setTavilyKey("");
      setClearDeepseekKey(false);
      setClearOpenaiKey(false);
      setClearAnthropicKey(false);
      setClearTavilyKey(false);
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
            <p>配置 Manager API、执行器项目 API 注入和 Tavily web search。执行器原生登录模式不会读取这里的 key。</p>
          </div>
        </div>
        <div className="settings-provider-grid">
          <div className="settings-provider-card">
            <div className="settings-provider-card-header">
              <div>
                <strong>Manager API</strong>
                <span>Manager、reviewer、library summarizer 使用。默认可填 DeepSeek Anthropic-compatible 地址，也可以手动换成兼容地址。</span>
              </div>
              <em>{appSettingsQuery.data?.deepseek.api_key_configured ? "key configured" : "key missing"}</em>
            </div>
            <div className="settings-form-grid compact">
              <label className="settings-field">
                <span>API key</span>
                <input
                  type="password"
                  value={deepseekKey}
                  onChange={(event) => setDeepseekKey(event.target.value)}
                  disabled={clearDeepseekKey}
                  placeholder={appSettingsQuery.data?.deepseek.api_key_configured ? "已配置，留空保持不变" : "输入 Manager API key"}
                />
              </label>
              {appSettingsQuery.data?.deepseek.api_key_configured ? (
                <label className="settings-check">
                  <input
                    type="checkbox"
                    checked={clearDeepseekKey}
                    onChange={(event) => setClearDeepseekKey(event.target.checked)}
                  />
                  <span>清除已保存的 Manager API key</span>
                </label>
              ) : null}
              <label className="settings-field">
                <span>Anthropic-compatible base URL</span>
                <input value={deepseekApiBaseUrl} onChange={(event) => setDeepseekApiBaseUrl(event.target.value)} />
              </label>
              <label className="settings-field">
                <span>Manager model</span>
                <input value={managerModel} onChange={(event) => setManagerModel(event.target.value)} />
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
                <span>Default executor model</span>
                <input value={executorModel} onChange={(event) => setExecutorModel(event.target.value)} />
              </label>
            </div>
          </div>

          <div className="settings-provider-card">
            <div className="settings-provider-card-header">
              <div>
                <strong>Pi Project API</strong>
                <span>用于默认 Pi 执行器。这里不是原生登录，而是 wrapper 注入项目 API。</span>
              </div>
              <em>best compatibility</em>
            </div>
            <div className="settings-form-grid compact">
              <label className="settings-field">
                <span>Pi provider base URL</span>
                <input value={piDeepseekBaseUrl} onChange={(event) => setPiDeepseekBaseUrl(event.target.value)} />
              </label>
              <div className="settings-inline-help">
                API key 复用 Manager API key。Pi 当前只支持 project_api，UI 中标记为最佳兼容。
              </div>
            </div>
          </div>

          <div className="settings-provider-card">
            <div className="settings-provider-card-header">
              <div>
                <strong>OpenAI-Compatible Executor API</strong>
                <span>用于 OpenCode project_api 注入。可填写 OpenAI 或任何 OpenAI-compatible 网关地址。</span>
              </div>
              <em>{appSettingsQuery.data?.openai.api_key_configured ? "key configured" : "optional"}</em>
            </div>
            <div className="settings-form-grid compact">
              <label className="settings-field">
                <span>API key</span>
                <input
                  type="password"
                  value={openaiKey}
                  onChange={(event) => setOpenaiKey(event.target.value)}
                  disabled={clearOpenaiKey}
                  placeholder={appSettingsQuery.data?.openai.api_key_configured ? "已配置，留空保持不变" : "输入 OpenAI-compatible key"}
                />
              </label>
              {appSettingsQuery.data?.openai.api_key_configured ? (
                <label className="settings-check">
                  <input
                    type="checkbox"
                    checked={clearOpenaiKey}
                    onChange={(event) => setClearOpenaiKey(event.target.checked)}
                  />
                  <span>清除已保存的 OpenAI-compatible key</span>
                </label>
              ) : null}
              <label className="settings-field">
                <span>Base URL</span>
                <input value={openaiApiBaseUrl} onChange={(event) => setOpenaiApiBaseUrl(event.target.value)} />
              </label>
            </div>
          </div>

          <div className="settings-provider-card">
            <div className="settings-provider-card-header">
              <div>
                <strong>Anthropic-Compatible Executor API</strong>
                <span>暂未使用，仅预留给未来 Anthropic-compatible 项目 API。Claude Code 当前仅支持原生 CLI 登录，不会注入这里的 key。</span>
              </div>
              <em>{appSettingsQuery.data?.anthropic.api_key_configured ? "reserved key configured" : "reserved / unused"}</em>
            </div>
            <div className="settings-form-grid compact">
              <label className="settings-field">
                <span>API key</span>
                <input
                  type="password"
                  value={anthropicKey}
                  onChange={(event) => setAnthropicKey(event.target.value)}
                  disabled={clearAnthropicKey}
                  placeholder={appSettingsQuery.data?.anthropic.api_key_configured ? "已配置，留空保持不变" : "输入 Anthropic-compatible key"}
                />
              </label>
              {appSettingsQuery.data?.anthropic.api_key_configured ? (
                <label className="settings-check">
                  <input
                    type="checkbox"
                    checked={clearAnthropicKey}
                    onChange={(event) => setClearAnthropicKey(event.target.checked)}
                  />
                  <span>清除已保存的 Anthropic-compatible key</span>
                </label>
              ) : null}
              <label className="settings-field">
                <span>Base URL</span>
                <input value={anthropicApiBaseUrl} onChange={(event) => setAnthropicApiBaseUrl(event.target.value)} />
              </label>
            </div>
          </div>

          <div className="settings-provider-card">
            <div className="settings-provider-card-header">
              <div>
                <strong>Web Search</strong>
                <span>Tavily 仅供 Manager 搜索使用，不参与执行器 API 注入。</span>
              </div>
              <em>{webSearchEnabled ? "enabled" : "disabled"}</em>
            </div>
            <div className="settings-form-grid compact">
              <label className="settings-field">
                <span>Tavily key</span>
                <input
                  type="password"
                  value={tavilyKey}
                  onChange={(event) => setTavilyKey(event.target.value)}
                  disabled={clearTavilyKey}
                  placeholder={appSettingsQuery.data?.web_search.api_key_configured ? "已配置，留空保持不变" : "输入 Tavily API key"}
                />
              </label>
              {appSettingsQuery.data?.web_search.api_key_configured ? (
                <label className="settings-check">
                  <input
                    type="checkbox"
                    checked={clearTavilyKey}
                    onChange={(event) => setClearTavilyKey(event.target.checked)}
                  />
                  <span>清除已保存的 Tavily key</span>
                </label>
              ) : null}
              <label className="settings-field">
                <span>Tavily base</span>
                <input value={tavilyBaseUrl} onChange={(event) => setTavilyBaseUrl(event.target.value)} />
              </label>
              <label className="settings-check">
                <input type="checkbox" checked={webSearchEnabled} onChange={(event) => setWebSearchEnabled(event.target.checked)} />
                <span>启用 Tavily web search</span>
              </label>
            </div>
          </div>
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

      <ExecutorProfilesSection />

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
