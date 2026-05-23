"use client";

import { CardStatus } from "@/lib/types";

const STATUS_LABEL_MAP: Record<string, string> = {
  proposed: "Proposed",
  planned: "Planned",
  running: "Running",
  reviewing: "Reviewing",
  needs_review: "Needs Review",
  accepted: "Accepted",
  rejected: "Rejected",
  stale: "Stale",
  superseded: "Superseded",
  cancelled: "Cancelled",
  failed: "Failed",
};

export function CardStatusBadge({ status }: { status: CardStatus | string }) {
  return <span className={`status-badge status-${status}`}>{STATUS_LABEL_MAP[status] ?? status}</span>;
}
