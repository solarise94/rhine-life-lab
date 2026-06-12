"use client";

import { useState } from "react";
import { SkillHubPanel } from "./SkillHubPanel";
import { McpHubPanel } from "./McpHubPanel";
import { CapabilityInstallPanel } from "./CapabilityInstallPanel";

interface CapabilitiesPanelProps {
  projectId: string;
}

export function CapabilitiesPanel({ projectId }: CapabilitiesPanelProps) {
  const [focusSkillId, setFocusSkillId] = useState<string | null>(null);
  const [focusMcpId, setFocusMcpId] = useState<string | null>(null);

  function handleInstalled(kind: "skill" | "mcp", installedId: string) {
    if (kind === "skill") {
      setFocusSkillId(installedId);
    } else {
      setFocusMcpId(installedId);
    }
  }

  return (
    <div className="stack">
      <SkillHubPanel projectId={projectId} focusId={focusSkillId} />
      <McpHubPanel projectId={projectId} focusId={focusMcpId} />
      <CapabilityInstallPanel projectId={projectId} onInstalled={handleInstalled} />
    </div>
  );
}
