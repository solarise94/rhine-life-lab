"use client";

import { CardStatus } from "@/lib/types";

const STATUS_LABEL_MAP: Record<string, string> = {
  proposed: "已提议",
  planned: "已计划",
  running: "运行中",
  reviewing: "审核中",
  needs_review: "待审核",
  accepted: "已接受",
  rejected: "已拒绝",
  stale: "已过时",
  superseded: "已替代",
  cancelled: "已取消",
  failed: "失败",
};

export function CardStatusBadge({ status }: { status: CardStatus | string }) {
  return <span className={`status-badge status-${status}`}>{STATUS_LABEL_MAP[status] ?? status}</span>;
}
