"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Database, Download, ExternalLink, FileCog, FileText, Folder, FolderUp, History, Link2, Loader2, Trash2, Unlink } from "lucide-react";

import { api } from "@/lib/api";
import { useProjectDataDirectoryExportHistory } from "@/lib/hooks";
import { Asset, DataDirectoryMount, ExecutionFileEntry, ExportHistoryEntry, ProjectFiles, WorkspaceEntry } from "@/lib/types";

const EXECUTION_CATEGORY_LABELS: Record<string, string> = {
  task_packet: "Task Packet",
  manifest: "Manifest",
  dependency_issue: "Dependency Issue",
  review_context: "Review Context",
  reviewer_trace: "Reviewer Trace",
  transcript: "Transcript",
  agent_trace: "Agent Trace",
  agent_output_timeline: "Agent Output Timeline",
  generated_script: "Generated Script",
};

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString();
}

function fallbackActiveAssets(items: Asset[]) {
  return items.filter((asset) => !["stale", "superseded", "rejected", "archived", "missing"].includes(asset.status));
}

function fallbackStaleAssets(items: Asset[]) {
  return items.filter((asset) => ["stale", "superseded", "rejected", "archived", "missing"].includes(asset.status));
}

export function FilesPanel({
  projectId,
  files,
  onRefresh,
  readOnly = false,
  onAttachAsset,
  onPreviewAsset,
}: {
  projectId: string;
  files?: ProjectFiles;
  onRefresh: () => Promise<void>;
  readOnly?: boolean;
  onAttachAsset: (asset: Asset) => void;
  onPreviewAsset?: (asset: Asset) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [clientError, setClientError] = useState<string | null>(null);
  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.uploadChatFile(projectId, file),
    onSuccess: async () => {
      setClientError(null);
      await onRefresh();
    },
    onError: () => {
      setClientError(null);
    },
  });
  const deleteUploadMutation = useMutation({
    mutationFn: (assetId: string) => api.deleteSessionUpload(projectId, assetId),
    onSuccess: async () => {
      setClientError(null);
      await onRefresh();
    },
    onError: (nextError) => {
      setClientError(nextError instanceof Error ? nextError.message : "删除上传文件失败。");
    },
  });
  const deleteAssetMutation = useMutation({
    mutationFn: (assetId: string) => api.deleteDataAsset(projectId, assetId),
    onSuccess: async () => {
      setClientError(null);
      await onRefresh();
    },
    onError: (nextError) => {
      setClientError(nextError instanceof Error ? nextError.message : "删除数据资产失败。");
    },
  });

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setClientError(null);
    if (readOnly) {
      setClientError("Auto mode 运行中，文件工作台处于只读状态。");
      return;
    }
    uploadMutation.mutate(file);
  }

  const activeDataAssets = files?.active_data_assets ?? fallbackActiveAssets(files?.data_assets ?? []);
  const staleDataAssets = files?.stale_data_assets ?? fallbackStaleAssets(files?.data_assets ?? []);

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-header">
          <h3>Files Workspace</h3>
          <span>
            {(files?.data_assets.length ?? 0) + (files?.session_uploads.length ?? 0)} tracked files
          </span>
        </div>
        <div className="panel-body stack">
          <div className="files-toolbar">
            <div className="files-toolbar-copy">
              <strong>上传到当前项目</strong>
              <span>上传的文件会进入 session uploads，并可直接加入 Manager 对话上下文。</span>
            </div>
            <div className="proposal-actions" style={{ marginTop: 0 }}>
              <input ref={fileInputRef} type="file" hidden onChange={handleFileChange} />
              <button
                className="btn primary"
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={readOnly || uploadMutation.isPending}
              >
                {uploadMutation.isPending ? <Loader2 size={14} className="spinning" /> : <FolderUp size={14} />}
                上传文件
              </button>
            </div>
          </div>
          {uploadMutation.error instanceof Error ? (
            <div className="notice-panel error">{uploadMutation.error.message}</div>
          ) : null}
          {clientError ? <div className="notice-panel error">{clientError}</div> : null}
          <div className="files-summary-grid">
            <div className="files-summary-card">
              <span>Data Assets</span>
              <strong>{files?.data_assets.length ?? 0}</strong>
            </div>
            <div className="files-summary-card">
              <span>Session Uploads</span>
              <strong>{files?.session_uploads.length ?? 0}</strong>
            </div>
            <div className="files-summary-card">
              <span>Execution Files</span>
              <strong>{files?.execution_files.length ?? 0}</strong>
            </div>
          </div>
        </div>
      </section>

      <DataDirectorySection projectId={projectId} onRefresh={onRefresh} readOnly={readOnly} />

      <AssetSection
        title="正在使用的数据资产"
        description="仍被 cards、报告或下游资产引用的数据资产，通常是最新 accepted 结果或当前输入。"
        items={activeDataAssets}
        projectId={projectId}
        emptyText="当前没有正在使用的数据资产。"
        onAttachAsset={onAttachAsset}
        onPreviewAsset={onPreviewAsset}
        onDeleteAsset={(asset) => deleteAssetMutation.mutate(asset.asset_id)}
        readOnly={readOnly}
        deletingAssetId={deleteAssetMutation.isPending ? deleteAssetMutation.variables : undefined}
      />
      <AssetSection
        title="过时的数据资产"
        description="被 rerun 替换、标记为 stale/superseded，或已不再被 cards 引用的数据资产。"
        items={staleDataAssets}
        projectId={projectId}
        emptyText="当前没有过时的数据资产。"
        onAttachAsset={onAttachAsset}
        onPreviewAsset={onPreviewAsset}
        onDeleteAsset={(asset) => deleteAssetMutation.mutate(asset.asset_id)}
        readOnly={readOnly}
        deletingAssetId={deleteAssetMutation.isPending ? deleteAssetMutation.variables : undefined}
      />
      <AssetSection
        title="Session Uploads"
        description="通过聊天或文件管理上传的临时文件。仍然作为资产跟踪，但默认单独归组。"
        items={files?.session_uploads ?? []}
        projectId={projectId}
        emptyText="当前没有会话上传文件。"
        onAttachAsset={onAttachAsset}
        onPreviewAsset={onPreviewAsset}
        onDeleteAsset={(asset) => deleteUploadMutation.mutate(asset.asset_id)}
        readOnly={readOnly}
        deletingAssetId={deleteUploadMutation.isPending ? deleteUploadMutation.variables : undefined}
      />
      <ExecutionFilesSection projectId={projectId} items={files?.execution_files ?? []} />
      <ExportHistorySection projectId={projectId} />
    </div>
  );
}

function ExportHistorySection({ projectId }: { projectId: string }) {
  const [expanded, setExpanded] = useState(false);
  const historyQuery = useProjectDataDirectoryExportHistory(projectId, expanded);

  function handleToggle() {
    setExpanded((v) => !v);
  }

  const items = historyQuery.data?.items ?? [];

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>导出历史</h3>
        <span>{items.length} records</span>
      </div>
      <div className="panel-body stack">
        <button type="button" className="btn secondary" onClick={handleToggle}>
          <History size={14} />
          {expanded ? "收起" : "查看导出历史"}
        </button>
        {expanded ? (
          historyQuery.isLoading ? (
            <div className="browser-empty">加载中...</div>
          ) : items.length === 0 ? (
            <div className="empty-state">暂无导出记录。</div>
          ) : (
            <div className="files-execution-list">
              {items.map((item: ExportHistoryEntry, idx: number) => (
                <div key={idx} className="files-execution-row">
                  <div className="files-execution-main">
                    <div className="files-asset-icon" style={{ width: 34, height: 34 }}>
                      <Download size={15} />
                    </div>
                    <div className="files-execution-meta">
                      <strong>{item.asset_id}</strong>
                      <span className="muted">{item.actor}</span>
                      <div className="muted files-path">源: {item.source_path}</div>
                      <div className="muted files-path">目标: {item.destination_path}</div>
                    </div>
                  </div>
                  <div className="files-execution-side">
                    <span>{item.exported_at}</span>
                  </div>
                </div>
              ))}
            </div>
          )
        ) : null}
      </div>
    </section>
  );
}

function DataDirectorySection({ projectId, onRefresh, readOnly = false }: { projectId: string; onRefresh: () => Promise<void>; readOnly?: boolean }) {
  const queryClient = useQueryClient();
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [currentPath, setCurrentPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [registerSuccess, setRegisterSuccess] = useState<string | null>(null);
  const [detachError, setDetachError] = useState<string | null>(null);

  const mountQuery = useQuery({
    queryKey: ["data-directory", projectId],
    queryFn: () => api.getProjectDataDirectory(projectId),
  });

  const mount: DataDirectoryMount | null = mountQuery.data?.data_directory ?? null;
  const isMounted = mount != null;

  const detachMutation = useMutation({
    mutationFn: () => api.deleteProjectDataDirectory(projectId),
    onSuccess: async () => {
      setDetachError(null);
      await queryClient.invalidateQueries({ queryKey: ["data-directory", projectId] });
      await onRefresh();
    },
    onError: (err: Error) => {
      setDetachError(err.message);
    },
  });

  const registerMutation = useMutation({
    mutationFn: (path: string) => api.registerDataDirectoryAsset(projectId, { path }),
    onSuccess: async () => {
      setRegisterError(null);
      setRegisterSuccess("已注册为数据资产 (data_mount/...)。");
      await onRefresh();
      setTimeout(() => setRegisterSuccess(null), 3000);
    },
    onError: (err: Error) => {
      setRegisterSuccess(null);
      setRegisterError(err.message);
    },
  });

  useEffect(() => {
    if (!isMounted) return;
    setLoading(true);
    setListError(null);
    let cancelled = false;
    api
      .listProjectDataDirectoryEntries(projectId, currentPath, "all")
      .then((res) => {
        if (!cancelled) {
          if ("available" in res && res.available === false) {
            setListError("数据目录不可用");
            setEntries([]);
          } else {
            setEntries(res.items);
          }
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setListError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, currentPath, isMounted]);

  if (mountQuery.isLoading) {
    return (
      <section className="panel">
        <div className="panel-header"><h3>数据目录</h3></div>
        <div className="panel-body"><div className="browser-empty">加载中...</div></div>
      </section>
    );
  }

  if (!isMounted) {
    return (
      <section className="panel">
        <div className="panel-header">
          <h3>数据目录</h3>
          <span>未挂载</span>
        </div>
        <div className="panel-body stack">
          <div className="muted" style={{ fontSize: 13 }}>
            此项目没有挂载数据目录。在项目设置中可以挂载一个已有的服务器数据目录，用于输入数据和结果导出。
          </div>
        </div>
      </section>
    );
  }

  const pathParts = currentPath ? currentPath.split("/").filter(Boolean) : [];
  const mountLabel = mount.path || mount.resolved_path.split("/").slice(-1)[0] || "data";
  const isAvailable = mountQuery.data?.available !== false;

  return (
    <section className="panel">
      <div className="panel-header">
        <h3>数据目录</h3>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Database size={14} style={{ marginRight: 4 }} />
          {mountLabel} — {isAvailable ? `${entries.length} entries` : "不可用"}
          <button
            type="button"
            className="btn danger"
            style={{ padding: "2px 8px", fontSize: 12 }}
            onClick={() => {
              if (window.confirm("解除挂载将移除数据目录关联，但不会删除服务器上的目录。data_mount/ 资产将标记为不可用。确认解除挂载？")) {
                detachMutation.mutate();
              }
            }}
            disabled={readOnly || detachMutation.isPending}
            title="解除数据目录挂载"
          >
            {detachMutation.isPending ? <Loader2 size={12} className="spinning" /> : <Unlink size={12} />}
            解除挂载
          </button>
        </span>
      </div>
      <div className="panel-body stack">
        {!isAvailable ? (
          <div className="notice-panel error">
            <div>数据目录不可用。目录可能已被删除或无法访问。</div>
            <a href={`/projects/${projectId}/settings`} className="btn secondary" style={{ marginTop: 8, display: "inline-flex" }}>
              <Link2 size={14} />
              前往项目设置重新挂载
            </a>
          </div>
        ) : null}
        {listError ? <div className="notice-panel error">{listError}</div> : null}
        {detachError ? <div className="notice-panel error">{detachError}</div> : null}
        {registerError ? <div className="notice-panel error">{registerError}</div> : null}
        {registerSuccess ? <div className="notice-panel success">{registerSuccess}</div> : null}
        {isAvailable ? (
          <>
            <div className="directory-browser-breadcrumb" style={{ padding: 0, border: 0 }}>
          <button
            type="button"
            className="breadcrumb-root"
            onClick={() => setCurrentPath("")}
            disabled={currentPath === ""}
          >
            data_mount/
          </button>
          {pathParts.map((part, idx) => (
            <span key={idx} className="breadcrumb-part">
              <span>/</span>
              <button type="button" onClick={() => setCurrentPath(pathParts.slice(0, idx + 1).join("/"))}>
                {part}
              </button>
            </span>
          ))}
        </div>
        <div className="directory-browser-list" style={{ maxHeight: 320 }}>
          {loading ? <div className="browser-empty">加载中...</div> : null}
          {!loading && currentPath !== "" ? (
            <button type="button" className="browser-entry browser-up" onClick={() => setCurrentPath(pathParts.slice(0, -1).join("/"))}>
              <ChevronLeft size={16} />
              ..
            </button>
          ) : null}
          {!loading && entries.length === 0 && currentPath === "" ? (
            <div className="browser-empty">数据目录为空</div>
          ) : null}
          {entries.map((entry) => (
            <div key={entry.name} className="browser-entry" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button
                type="button"
                className="browser-entry"
                style={{ flex: 1, display: "flex", alignItems: "center", gap: 8, padding: 0, border: 0, background: "transparent" }}
                onClick={() => {
                  if (entry.kind === "directory") {
                    setCurrentPath(currentPath ? `${currentPath}/${entry.name}` : entry.name);
                  }
                }}
              >
                {entry.kind === "directory" ? <Folder size={16} /> : <FileText size={16} />}
                <span className="entry-name">{entry.name}</span>
                {entry.size_bytes != null ? <span className="entry-badge">{formatBytes(entry.size_bytes)}</span> : null}
              </button>
              {entry.kind === "file" ? (
                <button
                  type="button"
                  className="btn secondary"
                  style={{ padding: "2px 8px", fontSize: 12 }}
                  onClick={() => {
                    const fullPath = currentPath ? `${currentPath}/${entry.name}` : entry.name;
                    registerMutation.mutate(fullPath);
                  }}
                  disabled={readOnly || registerMutation.isPending}
                  title="注册为数据资产 (data_mount/...)，可作为 card 输入"
                >
                  {registerMutation.isPending && registerMutation.variables === (currentPath ? `${currentPath}/${entry.name}` : entry.name) ? (
                    <Loader2 size={12} className="spinning" />
                  ) : (
                    <Link2 size={12} />
                  )}
                  注册资产
                </button>
              ) : null}
            </div>
          ))}
        </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

function AssetSection({
  title,
  description,
  items,
  projectId,
  emptyText,
  onAttachAsset,
  onPreviewAsset,
  onDeleteAsset,
  readOnly = false,
  deletingAssetId,
}: {
  title: string;
  description: string;
  items: Asset[];
  projectId: string;
  emptyText: string;
  onAttachAsset: (asset: Asset) => void;
  onPreviewAsset?: (asset: Asset) => void;
  onDeleteAsset?: (asset: Asset) => void;
  readOnly?: boolean;
  deletingAssetId?: string;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>{title}</h3>
        <span>{items.length}</span>
      </div>
      <div className="panel-body stack">
        <div className="muted" style={{ fontSize: 13 }}>{description}</div>
        {items.length ? (
          <div className="files-asset-grid">
            {items.map((asset) => (
              <div key={asset.asset_id} className="files-asset-card">
                <div className="files-asset-head">
                  <div className="files-asset-icon">
                    <FileText size={15} />
                  </div>
                  <div className="files-asset-meta">
                    <strong>{asset.title}</strong>
                    <span>{asset.asset_type}</span>
                  </div>
                </div>
                <div className="muted files-path">{asset.path}</div>
                <div style={{ fontSize: 13, lineHeight: 1.6 }}>{asset.summary}</div>
                <div className="files-asset-tags">
                  <span className="pill">{asset.status}</span>
                  {asset.created_by_run ? <span className="pill">run {asset.created_by_run}</span> : null}
                </div>
                <div className="proposal-actions">
                  <button className="btn secondary" type="button" onClick={() => onPreviewAsset?.(asset)}>
                    <FileText size={14} />
                    预览
                  </button>
                  <button className="btn secondary" type="button" onClick={() => onAttachAsset(asset)}>
                    <Link2 size={14} />
                    加入聊天上下文
                  </button>
                  <a className="btn secondary" href={api.getResultAssetContentUrl(projectId, asset.asset_id)} target="_blank" rel="noreferrer">
                    <Download size={14} />
                    下载
                  </a>
                  {onDeleteAsset ? (
                    <button
                      className="btn danger"
                      type="button"
                      onClick={() => onDeleteAsset(asset)}
                      disabled={readOnly || deletingAssetId === asset.asset_id}
                    >
                      {deletingAssetId === asset.asset_id ? <Loader2 size={14} className="spinning" /> : <Trash2 size={14} />}
                      删除
                    </button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">{emptyText}</div>
        )}
      </div>
    </section>
  );
}

function ExecutionFilesSection({ projectId, items }: { projectId: string; items: ExecutionFileEntry[] }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Execution Files</h3>
        <span>{items.length}</span>
      </div>
      <div className="panel-body stack">
        <div className="muted" style={{ fontSize: 13 }}>
          执行痕迹文件仅用于审计和下载，不会进入 Manager 的默认上下文。
        </div>
        {items.length ? (
          <div className="files-execution-list">
            {items.map((item) => (
              <div key={item.path} className="files-execution-row">
                <div className="files-execution-main">
                  <div className="files-asset-icon" style={{ width: 34, height: 34 }}>
                    <FileCog size={15} />
                  </div>
                  <div className="files-execution-meta">
                    <strong>{item.name}</strong>
                    <span>{EXECUTION_CATEGORY_LABELS[item.category] ?? item.category}</span>
                    <div className="muted files-path">{item.path}</div>
                  </div>
                </div>
                <div className="files-execution-side">
                  <span>{formatBytes(item.size_bytes)}</span>
                  <span>{item.run_id ? `run ${item.run_id}` : "script"}</span>
                  <span>{formatTime(item.updated_at)}</span>
                  <a className="btn secondary" href={api.getExecutionFileContentUrl(projectId, item.path)} target="_blank" rel="noreferrer">
                    <Download size={14} />
                    下载
                  </a>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">当前没有执行文件。</div>
        )}
      </div>
    </section>
  );
}
