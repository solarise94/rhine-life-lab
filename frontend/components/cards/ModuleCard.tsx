"use client";

import { CheckCircle2, Play } from "lucide-react";

import { Card } from "@/lib/types";
import { CardStatusBadge } from "./CardStatusBadge";

export function ModuleCard({
  card,
  active,
  onSelect,
  onStartRun,
  onReviewRun,
}: {
  card: Card;
  active: boolean;
  onSelect: (card: Card) => void;
  onStartRun: (card: Card) => void;
  onReviewRun: (card: Card) => void;
}) {
  return (
    <button type="button" className={`task-card ${active ? "active" : ""}`} onClick={() => onSelect(card)}>
      <div className="status-row">
        <CardStatusBadge status={card.status} />
        {card.aggregate_status ? <span className="pill">{card.aggregate_status}</span> : null}
        <span className="muted">{card.card_type}</span>
      </div>
      <h4>{card.title}</h4>
      <div className="muted">{card.summary}</div>
      {card.progress_note ? (
        <div className="pill" style={{ marginTop: 10 }}>
          {card.progress_note}
        </div>
      ) : null}
      <div className="inline-actions">
        {card.status === "planned" ? (
          <button
            type="button"
            className="btn secondary"
            onClick={(event) => {
              event.stopPropagation();
              onStartRun(card);
            }}
          >
            <Play size={16} />
            开始执行
          </button>
        ) : null}
        {card.status === "needs_review" && card.linked_runs.length ? (
          <button
            type="button"
            className="btn secondary"
            onClick={(event) => {
              event.stopPropagation();
              onReviewRun(card);
            }}
          >
            <CheckCircle2 size={16} />
            接受结果
          </button>
        ) : null}
      </div>
    </button>
  );
}
