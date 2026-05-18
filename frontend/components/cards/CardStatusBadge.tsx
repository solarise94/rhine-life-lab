"use client";

import { CardStatus } from "@/lib/types";

export function CardStatusBadge({ status }: { status: CardStatus | string }) {
  return <span className={`status-badge status-${status}`}>{status}</span>;
}

