"use client";

import { FlaskConical } from "lucide-react";
import { ProjectSummary } from "@/lib/types";

export function ProjectHeader({ summary, title }: { summary: ProjectSummary; title: string }) {
  return (
    <header className="header">
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 12,
            background: "linear-gradient(135deg, rgba(59,130,246,0.1), rgba(34,197,94,0.08))",
            border: "1px solid rgba(59,130,246,0.15)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--blue)",
            flexShrink: 0,
          }}
        >
          <FlaskConical size={20} />
        </div>
        <div>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, letterSpacing: "-0.3px", color: "var(--text)" }}>
            {summary.name}
          </h2>
          <div className="muted" style={{ fontSize: 12, marginTop: 2, fontWeight: 500 }}>{title}</div>
        </div>
      </div>
      <div className="header-meta">
        <span
          className="pill"
          style={{
            fontSize: 11,
            fontWeight: 600,
            background: summary.status === "active" ? "var(--green-bg)" : "var(--gray-bg)",
            color: summary.status === "active" ? "var(--green-dark)" : "var(--gray-dark)",
            borderColor: summary.status === "active" ? "var(--green-border)" : "var(--gray-border)",
          }}
        >
          {summary.status}
        </span>
        <span className="pill" style={{ fontSize: 11 }}>
          v{summary.schema_version}
        </span>
      </div>
    </header>
  );
}
