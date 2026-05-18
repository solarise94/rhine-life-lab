"use client";

import Link from "next/link";
import { BarChart3, FileText, FolderGit2, Layers3, Sparkles } from "lucide-react";

const primary = [
  { href: "tasks", label: "Tasks", icon: Layers3 },
  { href: "results", label: "Results", icon: BarChart3 },
  { href: "report", label: "Report", icon: FileText },
];

export function SideNav({ projectId, current }: { projectId: string; current: string }) {
  return (
    <aside className="side-nav">
      <div className="nav-brand">
        <h1>Blueprint RE v3</h1>
        <p>Git-native project manager for bioinformatics analysis</p>
      </div>
      <div className="nav-section-label">Project</div>
      <div className="nav-links">
        {primary.map((item) => {
          const Icon = item.icon;
          const href = `/projects/${projectId}/${item.href}`;
          return (
            <Link key={item.href} href={href} className={`nav-link ${current === item.href ? "active" : ""}`}>
              <Icon size={16} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
      <div className="nav-section-label">Advanced</div>
      <div className="nav-secondary">
        <Link href={`/projects/${projectId}/advanced`} className={`nav-link ${current === "advanced" ? "active" : ""}`}>
          <FolderGit2 size={16} />
          <span>Advanced</span>
        </Link>
      </div>
      <div className="nav-section-label">Mode</div>
      <div className="nav-secondary">
        <div className="nav-link active">
          <Sparkles size={16} />
          <span>Manager AI</span>
        </div>
      </div>
    </aside>
  );
}

