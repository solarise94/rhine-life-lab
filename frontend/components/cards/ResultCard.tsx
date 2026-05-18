"use client";

import { Card } from "@/lib/types";
import { CardStatusBadge } from "./CardStatusBadge";

export function ResultCard({ card, active, onSelect }: { card: Card; active: boolean; onSelect: (card: Card) => void }) {
  return (
    <button type="button" className={`task-card ${active ? "active" : ""}`} onClick={() => onSelect(card)}>
      <div className="status-row">
        <CardStatusBadge status={card.status} />
        <span className="muted">result</span>
      </div>
      <h4>{card.title}</h4>
      <div className="muted">{card.summary}</div>
    </button>
  );
}
