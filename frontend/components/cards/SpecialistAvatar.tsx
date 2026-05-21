"use client";

import { CardStatus } from "@/lib/types";

const STATUS_GRADIENTS: Record<string, [string, string]> = {
  proposed: ["#8b5cf6", "#a78bfa"],
  planned: ["#3b82f6", "#22d3ee"],
  running: ["#f59e0b", "#fbbf24"],
  reviewing: ["#f59e0b", "#22d3ee"],
  needs_review: ["#f59e0b", "#fbbf24"],
  accepted: ["#22c55e", "#34d399"],
  rejected: ["#ef4444", "#f87171"],
  failed: ["#ef4444", "#f87171"],
  stale: ["#9ca3af", "#d1d5db"],
  superseded: ["#9ca3af", "#d1d5db"],
  cancelled: ["#9ca3af", "#d1d5db"],
};

export function SpecialistAvatar({
  name,
  status,
  size = 44,
}: {
  name: string;
  status: CardStatus | string;
  size?: number;
}) {
  const initials = name
    .split(/[\s_\-]+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  const [c1, c2] = STATUS_GRADIENTS[status] ?? STATUS_GRADIENTS.planned;
  const glowOpacity = status === "running" || status === "reviewing" ? 0.5 : status === "proposed" ? 0.25 : 0.35;

  return (
    <div
      className="avatar-wrap"
      style={{ width: size, height: size, borderRadius: size * 0.27 }}
    >
      <div
        className="avatar-glow"
        style={{
          background: `radial-gradient(circle, ${c1}, ${c2})`,
          opacity: glowOpacity,
          inset: -3,
          borderRadius: size * 0.3,
        }}
      />
      <div
        className="avatar-inner"
        style={{
          background: `linear-gradient(135deg, ${c1}18, ${c2}18)`,
          border: `1.5px solid ${c1}35`,
          borderRadius: size * 0.27,
          color: c1,
          fontSize: size * 0.36,
        }}
      >
        {initials}
      </div>
    </div>
  );
}
