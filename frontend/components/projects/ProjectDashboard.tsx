"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Beaker, ChevronLeft, ChevronDown, ChevronRight, Folder, FolderOpen, Plus, Trash2 } from "lucide-react";

import { api } from "@/lib/api";
import { useCreateProjectMutation, useDeleteProjectMutation, useProjects } from "@/lib/hooks";
import { queryKeys } from "@/lib/query-keys";
import { ProjectSummary, WorkspaceEntry, WorkspaceRoot } from "@/lib/types";

function slugifyProjectId(value: string) {
  const slug = value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/g, "");
  return slug || "new-project";
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function totalCount(counts: Record<string, number>) {
  return Object.values(counts).reduce((sum, item) => sum + item, 0);
}

function ProjectRow({
  project,
  deleting,
  onOpen,
  onDelete,
}: {
  project: ProjectSummary;
  deleting: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="project-row">
      <button type="button" className="project-row-main" onClick={onOpen}>
        <div className="project-icon">
          <Beaker size={18} />
        </div>
        <div className="project-copy">
          <div className="project-title-line">
            <strong>{project.name}</strong>
            <span>{project.status}</span>
          </div>
          <p>{project.current_goal || "未设置项目目标"}</p>
          <div className="project-meta">
            <span>{project.project_id}</span>
            <span>{totalCount(project.card_counts)} cards</span>
            <span>{totalCount(project.result_counts)} results</span>
            <span>{formatDate(project.updated_at)}</span>
          </div>
        </div>
      </button>
      <div className="project-row-actions">
        <button type="button" className="btn secondary" onClick={onOpen}>
          <FolderOpen size={15} />
          打开
        </button>
        <button type="button" className="btn danger" onClick={onDelete} disabled={deleting}>
          <Trash2 size={15} />
          删除
        </button>
      </div>
    </div>
  );
}

export function ProjectDashboard() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const projectsQuery = useProjects();
  const createProjectMutation = useCreateProjectMutation();
  const deleteProjectMutation = useDeleteProjectMutation();
  const [isCreating, setIsCreating] = useState(false);

  // Basic project form state
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [projectIdTouched, setProjectIdTouched] = useState(false);
  const [currentGoal, setCurrentGoal] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [createdProjectId, setCreatedProjectId] = useState<string | null>(null);

  // Data directory mount state
  const [mountExpanded, setMountExpanded] = useState(false);
  const [dataRoots, setDataRoots] = useState<WorkspaceRoot[]>([]);
  const [selectedDataRoot, setSelectedDataRoot] = useState<WorkspaceRoot | null>(null);
  const [dataBrowserPath, setDataBrowserPath] = useState("");
  const [dataBrowserEntries, setDataBrowserEntries] = useState<WorkspaceEntry[]>([]);
  const [dataBrowserLoading, setDataBrowserLoading] = useState(false);
  const [dataBrowserError, setDataBrowserError] = useState<string | null>(null);
  const [selectedDataDirectory, setSelectedDataDirectory] = useState<{ root_id: string; path: string } | null>(null);

  const projects = useMemo(
    () =>
      [...(projectsQuery.data?.items ?? [])].sort((left, right) =>
        right.updated_at.localeCompare(left.updated_at)
      ),
    [projectsQuery.data]
  );
  const busy = createProjectMutation.isPending || deleteProjectMutation.isPending;
  const error =
    formError ||
    (projectsQuery.error instanceof Error ? projectsQuery.error.message : null) ||
    (createProjectMutation.error instanceof Error ? createProjectMutation.error.message : null) ||
    (deleteProjectMutation.error instanceof Error ? deleteProjectMutation.error.message : null);

  function resetForm() {
    setName("");
    setProjectId("");
    setProjectIdTouched(false);
    setCurrentGoal("");
    setFormError(null);
    setCreatedProjectId(null);
    setMountExpanded(false);
    setSelectedDataDirectory(null);
    setDataBrowserPath("");
    setDataBrowserEntries([]);
    setDataBrowserError(null);
  }

  function handleNameChange(value: string) {
    setName(value);
    if (!projectIdTouched) {
      setProjectId(slugifyProjectId(value));
    }
  }

  // Load workspace roots when mount section is expanded
  useEffect(() => {
    if (!mountExpanded) return;
    api.listWorkspaceRoots()
      .then((res) => {
        setDataRoots(res.items);
        if (res.items.length > 0 && !selectedDataRoot) {
          setSelectedDataRoot(res.items[0]);
        }
      })
      .catch((err: Error) => {
        setDataBrowserError(err.message);
      });
  }, [mountExpanded]);

  // Load directory entries when root or path changes
  useEffect(() => {
    if (!mountExpanded || !selectedDataRoot) return;
    setDataBrowserLoading(true);
    setDataBrowserError(null);
    let cancelled = false;
    api
      .listWorkspaceEntries(selectedDataRoot.root_id, dataBrowserPath, "directory")
      .then((res) => {
        if (!cancelled) {
          setDataBrowserEntries(res.items);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setDataBrowserError(err.message);
          setDataBrowserEntries([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setDataBrowserLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [mountExpanded, selectedDataRoot, dataBrowserPath]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextName = name.trim();
    const nextProjectId = slugifyProjectId(projectId || name);
    const nextGoal = currentGoal.trim();
    if (!nextName || !nextProjectId || !nextGoal) {
      setFormError("请填写项目名称、Project ID 和项目目标。");
      return;
    }
    setFormError(null);

    // Step 1: create the managed project
    const response = await createProjectMutation.mutateAsync({
      name: nextName,
      project_id: nextProjectId,
      current_goal: nextGoal,
    });

    // Step 2: if a data directory is selected, mount it
    if (selectedDataDirectory) {
      try {
        await api.updateProjectDataDirectory(
          response.project.project_id,
          {
            root_id: selectedDataDirectory.root_id,
            path: selectedDataDirectory.path,
          }
        );
      } catch (err) {
        // Mount failed but project was created; close form and show recovery banner
        setFormError(err instanceof Error ? `项目已创建，但挂载数据目录失败：${err.message}` : "项目已创建，但挂载数据目录失败。");
        setCreatedProjectId(response.project.project_id);
        await queryClient.invalidateQueries({ queryKey: queryKeys.projects });
        resetForm();
        setIsCreating(false);
        return;
      }
    }

    resetForm();
    setIsCreating(false);
    await queryClient.invalidateQueries({ queryKey: queryKeys.projects });
    router.push(`/projects/${response.project.project_id}/tasks`);
  }

  async function handleDelete(project: ProjectSummary) {
    const isManaged = project.root_kind === "managed_project_directory";
    const message = isManaged
      ? `删除项目 "${project.name}"？默认只从 Blueprint 移除，不会删除服务器上的目录。`
      : `删除项目 "${project.name}"？这会删除 workspace/${project.project_id}。`;
    if (!window.confirm(message)) {
      return;
    }
    await deleteProjectMutation.mutateAsync(project.project_id);
  }

  const dataPathParts = dataBrowserPath ? dataBrowserPath.split("/").filter(Boolean) : [];

  return (
    <main className="projects-page">
      <section className="projects-header">
        <div>
          <h1>Projects</h1>
          <p>管理项目 workspace，打开后进入对应的 Sessions、Cards、文件和结果库。</p>
        </div>
        <div className="projects-header-actions">
          <button
            type="button"
            className="btn primary"
            onClick={() => {
              setIsCreating(true);
              setFormError(null);
            }}
          >
            <Plus size={16} />
            新建项目
          </button>
        </div>
      </section>

      {createdProjectId ? (
        <div className="notice-panel warning">
          <div>{formError}</div>
          <div className="project-form-actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn primary"
              onClick={() => router.push(`/projects/${createdProjectId}/tasks`)}
            >
              进入项目
            </button>
            <button
              type="button"
              className="btn secondary"
              onClick={() => setCreatedProjectId(null)}
            >
              关闭
            </button>
          </div>
        </div>
      ) : error ? (
        <div className="notice-panel error">{error}</div>
      ) : null}

      {isCreating ? (
        <section className="panel project-create-panel">
          <div className="panel-header">
            <h3>新建项目</h3>
            <button
              type="button"
              className="btn secondary"
              onClick={() => {
                setIsCreating(false);
                resetForm();
              }}
            >
              取消
            </button>
          </div>
          <form className="project-form" onSubmit={handleCreate}>
            <label>
              <span>项目名称</span>
              <input
                value={name}
                onChange={(event) => handleNameChange(event.target.value)}
                placeholder="RNA-seq Project"
                required
              />
            </label>
            <label>
              <span>Project ID</span>
              <input
                value={projectId}
                onChange={(event) => {
                  setProjectIdTouched(true);
                  setProjectId(slugifyProjectId(event.target.value));
                }}
                placeholder="rna-seq-project"
                required
              />
            </label>
            <label>
              <span>项目目标</span>
              <textarea
                value={currentGoal}
                onChange={(event) => setCurrentGoal(event.target.value)}
                placeholder="完成差异表达分析与下游解释"
                required
              />
            </label>

            {/* Optional data directory mount */}
            <div className="mount-section">
              <button
                type="button"
                className="mount-toggle"
                onClick={() => setMountExpanded((v) => !v)}
              >
                {mountExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                挂载数据目录 (可选)
              </button>
              {mountExpanded && (
                <div className="mount-browser">
                  {selectedDataDirectory ? (
                    <div className="mount-selection">
                      <span>
                        已选择：
                        <strong>
                          {selectedDataDirectory.root_id} / {selectedDataDirectory.path || "."}
                        </strong>
                      </span>
                      <button
                        type="button"
                        className="btn secondary"
                        onClick={() => setSelectedDataDirectory(null)}
                      >
                        清除选择
                      </button>
                    </div>
                  ) : (
                    <div className="mount-hint">
                      在下方浏览并选择一个已有的服务器数据目录，用于输入数据和结果导出。
                    </div>
                  )}

                  {dataBrowserError ? (
                    <div className="notice-panel error">{dataBrowserError}</div>
                  ) : null}

                  <div className="directory-browser-toolbar">
                    <select
                      value={selectedDataRoot?.root_id ?? ""}
                      onChange={(e) => {
                        const root = dataRoots.find((r) => r.root_id === e.target.value);
                        setSelectedDataRoot(root || null);
                        setDataBrowserPath("");
                        setSelectedDataDirectory(null);
                      }}
                      disabled={dataBrowserLoading}
                    >
                      {dataRoots.map((r) => (
                        <option key={r.root_id} value={r.root_id}>
                          {r.label} ({r.path})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="directory-browser-breadcrumb">
                    <button
                      type="button"
                      className="breadcrumb-root"
                      onClick={() => setDataBrowserPath("")}
                      disabled={dataBrowserPath === ""}
                    >
                      {selectedDataRoot?.label ?? "Root"}
                    </button>
                    {dataPathParts.map((part, idx) => (
                      <span key={idx} className="breadcrumb-part">
                        <span>/</span>
                        <button
                          type="button"
                          onClick={() =>
                            setDataBrowserPath(dataPathParts.slice(0, idx + 1).join("/"))
                          }
                        >
                          {part}
                        </button>
                      </span>
                    ))}
                  </div>

                  <div className="directory-browser-list" style={{ maxHeight: 240 }}>
                    {dataBrowserLoading ? (
                      <div className="browser-empty">加载中...</div>
                    ) : null}
                    {!dataBrowserLoading && dataBrowserPath !== "" ? (
                      <button
                        type="button"
                        className="browser-entry browser-up"
                        onClick={() =>
                          setDataBrowserPath(dataPathParts.slice(0, -1).join("/"))
                        }
                      >
                        <ChevronLeft size={16} />
                        ..
                      </button>
                    ) : null}
                    {!dataBrowserLoading && dataBrowserEntries.length === 0 && dataBrowserPath === "" ? (
                      <div className="browser-empty">
                        空目录
                        <span className="muted-hint">当前目录为空，仍可作为挂载点。</span>
                      </div>
                    ) : null}
                    {dataBrowserEntries.map((entry) => (
                      <button
                        key={entry.name}
                        type="button"
                        className="browser-entry"
                        onClick={() =>
                          setDataBrowserPath(
                            dataBrowserPath
                              ? `${dataBrowserPath}/${entry.name}`
                              : entry.name
                          )
                        }
                      >
                        <Folder size={16} />
                        <span className="entry-name">{entry.name}</span>
                        {entry.is_empty ? <span className="entry-badge">空</span> : null}
                      </button>
                    ))}
                  </div>
                  {!selectedDataDirectory && selectedDataRoot && (
                    <div className="directory-browser-toolbar" style={{ marginTop: 8 }}>
                      <button
                        type="button"
                        className="btn secondary"
                        onClick={() => {
                          if (selectedDataRoot) {
                            setSelectedDataDirectory({
                              root_id: selectedDataRoot.root_id,
                              path: dataBrowserPath,
                            });
                          }
                        }}
                        disabled={dataBrowserLoading}
                      >
                        使用当前目录
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="project-form-actions">
              <button type="submit" className="btn primary" disabled={busy}>
                创建并打开
              </button>
            </div>
          </form>
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-header">
          <h3>项目列表</h3>
          <span>{projects.length} projects</span>
        </div>
        <div className="project-list">
          {projectsQuery.isLoading ? <div className="project-empty">加载中...</div> : null}
          {!projectsQuery.isLoading && !projects.length ? (
            <div className="project-empty">还没有项目。</div>
          ) : null}
          {projects.map((project) => (
            <ProjectRow
              key={project.project_id}
              project={project}
              deleting={deleteProjectMutation.isPending}
              onOpen={() => router.push(`/projects/${project.project_id}/tasks`)}
              onDelete={() => handleDelete(project)}
            />
          ))}
        </div>
      </section>
    </main>
  );
}
