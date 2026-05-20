"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Beaker, FolderOpen, Plus, Trash2 } from "lucide-react";

import { useCreateProjectMutation, useDeleteProjectMutation, useProjects } from "@/lib/hooks";
import { ProjectSummary } from "@/lib/types";

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
  const projectsQuery = useProjects();
  const createProjectMutation = useCreateProjectMutation();
  const deleteProjectMutation = useDeleteProjectMutation();
  const [isCreating, setIsCreating] = useState(false);
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [projectIdTouched, setProjectIdTouched] = useState(false);
  const [currentGoal, setCurrentGoal] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const projects = useMemo(
    () => [...(projectsQuery.data?.items ?? [])].sort((left, right) => right.updated_at.localeCompare(left.updated_at)),
    [projectsQuery.data],
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

  async function handleDelete(project: ProjectSummary) {
    if (!window.confirm(`删除项目 "${project.name}"？这会删除 workspace/${project.project_id}。`)) {
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
              <input value={name} onChange={(event) => handleNameChange(event.target.value)} placeholder="RNA-seq Project" />
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

      <section className="panel">
        <div className="panel-header">
          <h3>项目列表</h3>
          <span>{projects.length} projects</span>
        </div>
        <div className="project-list">
          {projectsQuery.isLoading ? <div className="project-empty">加载中...</div> : null}
          {!projectsQuery.isLoading && !projects.length ? <div className="project-empty">还没有项目。</div> : null}
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
