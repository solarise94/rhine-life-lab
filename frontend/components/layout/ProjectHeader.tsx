"use client";

import { ProjectSummary } from "@/lib/types";

export function ProjectHeader({ summary, title }: { summary: ProjectSummary; title: string }) {
  return (
    <header className="header">
      <div>
        <h2>{summary.name}</h2>
        <div className="muted">{title}</div>
      </div>
      <div className="header-meta">
        <div className="pill">Status: {summary.status}</div>
        <div className="pill">Schema: {summary.schema_version}</div>
        <div className="pill">Updated: {summary.updated_at}</div>
      </div>
    </header>
  );
}

