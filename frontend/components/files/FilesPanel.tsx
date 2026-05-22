"use client";

import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Download, FileCog, FileText, FolderUp, Link2, Loader2, Trash2 } from "lucide-react";

import { api } from "@/lib/api";
import { Asset, ExecutionFileEntry, ProjectFiles } from "@/lib/types";

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const EXECUTION_CATEGORY_LABELS: Record<string, string> = {
  task_packet: "Task Packet",
  manifest: "Manifest",
  review_context: "Review Context",
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

export function FilesPanel({
  projectId,
  files,
  onRefresh,
  onAttachAsset,
}: {
  projectId: string;
  files?: ProjectFiles;
  onRefresh: () => Promise<void>;
  onAttachAsset: (asset: Asset) => void;
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

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setClientError(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setClientError("文件超过 50MB，当前上传入口不支持。");
      return;
    }
    uploadMutation.mutate(file);
  }

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
                disabled={uploadMutation.isPending}
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

      <AssetSection
        title="Data Assets"
        description="正式数据资产。可下载，也可加入 Manager 上下文。"
        items={files?.data_assets ?? []}
        projectId={projectId}
        emptyText="当前没有正式数据资产。"
        onAttachAsset={onAttachAsset}
      />
      <AssetSection
        title="Session Uploads"
        description="通过聊天或文件管理上传的临时文件。仍然作为资产跟踪，但默认单独归组。"
        items={files?.session_uploads ?? []}
        projectId={projectId}
        emptyText="当前没有会话上传文件。"
        onAttachAsset={onAttachAsset}
        onDeleteAsset={(asset) => deleteUploadMutation.mutate(asset.asset_id)}
        deletingAssetId={deleteUploadMutation.isPending ? deleteUploadMutation.variables : undefined}
      />
      <ExecutionFilesSection projectId={projectId} items={files?.execution_files ?? []} />
    </div>
  );
}

function AssetSection({
  title,
  description,
  items,
  projectId,
  emptyText,
  onAttachAsset,
  onDeleteAsset,
  deletingAssetId,
}: {
  title: string;
  description: string;
  items: Asset[];
  projectId: string;
  emptyText: string;
  onAttachAsset: (asset: Asset) => void;
  onDeleteAsset?: (asset: Asset) => void;
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
                      disabled={deletingAssetId === asset.asset_id}
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
