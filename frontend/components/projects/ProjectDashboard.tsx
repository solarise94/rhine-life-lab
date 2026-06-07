"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Beaker, ChevronLeft, Folder, FolderOpen, Plus, Trash2 } from "lucide-react";

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

function DirectoryBrowserModal({
  onClose,
  onCreate,
  busy,
}: {
  onClose: () => void;
  onCreate: (payload: {
    root_id: string;
    parent_path: string;
    directory_name: string;
    project_id: string;
    name: string;
    current_goal: string;
  }) => Promise<void>;
  busy: boolean;
}) {
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  const [selectedRoot, setSelectedRoot] = useState<WorkspaceRoot | null>(null);
  const [currentPath, setCurrentPath] = useState("");
  const [entries, setEntries] = useState<WorkspaceEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [browserError, setBrowserError] = useState<string | null>(null);

  const [directoryName, setDirectoryName] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [projectIdTouched, setProjectIdTouched] = useState(false);
  const [currentGoal, setCurrentGoal] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    api.listWorkspaceRoots()
      .then((res) => {
        setRoots(res.items);
        if (res.items.length > 0) {
          setSelectedRoot(res.items[0]);
        }
      })
      .catch((err: Error) => {
        setBrowserError(err.message);
      });
  }, []);

  useEffect(() => {
    if (!selectedRoot) return;
    setLoading(true);
    setBrowserError(null);
    let cancelled = false;
    api
      .listWorkspaceEntries(selectedRoot.root_id, currentPath, "directory")
      .then((res) => {
        if (!cancelled) {
          setEntries(res.items);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setBrowserError(err.message);
          setEntries([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRoot, currentPath]);

  function handleNameChange(value: string) {
    setProjectName(value);
    if (!projectIdTouched) {
      setProjectId(slugifyProjectId(value));
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextDirName = directoryName.trim();
    const nextName = projectName.trim();
    const nextProjectId = slugifyProjectId(projectId || projectName);
    const nextGoal = currentGoal.trim();
    if (!selectedRoot) {
      setFormError("请选择一个根目录。");
      return;
    }
    if (!nextDirName) {
      setFormError("请输入目录名称。");
      return;
    }
    if (!nextName || !nextProjectId || !nextGoal) {
      setFormError("请填写项目名称、Project ID 和项目目标。");
      return;
    }
    setFormError(null);
    await onCreate({
      root_id: selectedRoot.root_id,
      parent_path: currentPath,
      directory_name: nextDirName,
      project_id: nextProjectId,
      name: nextName,
      current_goal: nextGoal,
    });
  }

  const pathParts = currentPath ? currentPath.split("/").filter(Boolean) : [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>新建服务器项目目录</h3>
          <button type="button" className="btn secondary" onClick={onClose}>
            取消
          </button>
        </div>

        {(browserError || formError) ? (
          <div className="notice-panel error">{browserError || formError}</div>
        ) : null}

        <form onSubmit={handleSubmit} className="directory-browser-form">
          <div className="directory-browser-pane">
            <div className="directory-browser-toolbar">
              <select
                value={selectedRoot?.root_id ?? ""}
                onChange={(e) => {
                  const root = roots.find((r) => r.root_id === e.target.value);
                  setSelectedRoot(root || null);
                  setCurrentPath("");
                }}
                disabled={loading}
              >
                {roots.map((r) => (
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
                onClick={() => setCurrentPath("")}
                disabled={currentPath === ""}
              >
                {selectedRoot?.label ?? "Root"}
              </button>
              {pathParts.map((part, idx) => (
                <span key={idx} className="breadcrumb-part">
                  <span>/</span>
                  <button
                    type="button"
                    onClick={() =>
                      setCurrentPath(pathParts.slice(0, idx + 1).join("/"))
                    }
                  >
                    {part}
                  </button>
                </span>
              ))}
            </div>

            <div className="directory-browser-list">
              {loading ? <div className="browser-empty">加载中...</div> : null}
              {!loading && currentPath !== "" ? (
                <button
                  type="button"
                  className="browser-entry browser-up"
                  onClick={() =>
                    setCurrentPath(pathParts.slice(0, -1).join("/"))
                  }
                >
                  <ChevronLeft size={16} />
                  ..
                </button>
              ) : null}
              {!loading && entries.length === 0 && currentPath === "" ? (
                <div className="browser-empty">空目录</div>
              ) : null}
              {entries.map((entry) => (
                <button
                  key={entry.name}
                  type="button"
                  className="browser-entry"
                  onClick={() => setCurrentPath(currentPath ? `${currentPath}/${entry.name}` : entry.name)}
                >
                  <Folder size={16} />
                  <span className="entry-name">{entry.name}</span>
                  {entry.is_empty ? <span className="entry-badge">空</span> : null}
                </button>
              ))}
            </div>
          </div>

          <div className="directory-browser-form-fields">
            <label>
              <span>目录名称</span>
              <input
                value={directoryName}
                onChange={(e) => setDirectoryName(e.target.value)}
                placeholder="my-project"
                required
              />
            </label>
            <label>
              <span>项目名称</span>
              <input
                value={projectName}
                onChange={(e) => handleNameChange(e.target.value)}
                placeholder="My Project"
                required
              />
            </label>
            <label>
              <span>Project ID</span>
              <input
                value={projectId}
                onChange={(e) => {
                  setProjectIdTouched(true);
                  setProjectId(slugifyProjectId(e.target.value));
                }}
                placeholder="my-project"
                required
              />
            </label>
            <label>
              <span>项目目标</span>
              <textarea
                value={currentGoal}
                onChange={(e) => setCurrentGoal(e.target.value)}
                placeholder="完成差异表达分析与下游解释"
                required
              />
            </label>

            <div className="directory-browser-actions">
              <button type="submit" className="btn primary" disabled={busy || loading}>
                创建并打开
              </button>
            </div>
          </div>
        </form>
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
  const [isCreatingFromDirectory, setIsCreatingFromDirectory] = useState(false);
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [projectIdTouched, setProjectIdTouched] = useState(false);
  const [currentGoal, setCurrentGoal] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

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
  }

  function handleNameChange(value: string) {
    setName(value);
    if (!projectIdTouched) {
      setProjectId(slugifyProjectId(value));
    }
  }

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
    const response = await createProjectMutation.mutateAsync({
      name: nextName,
      project_id: nextProjectId,
      current_goal: nextGoal,
    });
    resetForm();
    setIsCreating(false);
    router.push(`/projects/${response.project.project_id}/tasks`);
  }

  async function handleCreateFromDirectory(payload: {
    root_id: string;
    parent_path: string;
    directory_name: string;
    project_id: string;
    name: string;
    current_goal: string;
  }) {
    setFormError(null);
    try {
      const response = await api.createProjectFromDirectory(payload);
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      setIsCreatingFromDirectory(false);
      router.push(`/projects/${response.project.project_id}/tasks`);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "创建项目失败");
    }
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
          <button
            type="button"
            className="btn secondary"
            onClick={() => {
              setIsCreatingFromDirectory(true);
              setFormError(null);
            }}
          >
            <FolderOpen size={16} />
            新建服务器项目目录
          </button>
        </div>
      </section>

      {error ? <div className="notice-panel error">{error}</div> : null}

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
              />
            </label>
            <label>
              <span>项目目标</span>
              <textarea
                value={currentGoal}
                onChange={(event) => setCurrentGoal(event.target.value)}
                placeholder="完成差异表达分析与下游解释"
              />
            </label>
            <div className="project-form-actions">
              <button type="submit" className="btn primary" disabled={busy}>
                创建并打开
              </button>
            </div>
          </form>
        </section>
      ) : null}

      {isCreatingFromDirectory ? (
        <DirectoryBrowserModal
          onClose={() => setIsCreatingFromDirectory(false)}
          onCreate={handleCreateFromDirectory}
          busy={busy}
        />
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
