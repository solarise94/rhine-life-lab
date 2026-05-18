"use client";

import { Card } from "@/lib/types";
import { ModuleCard } from "./ModuleCard";
import { ResultCard } from "./ResultCard";
import { RunCard } from "./RunCard";

export function CardStream({
  cards,
  selectedCardId,
  onSelect,
  onStartRun,
  onReviewRun,
}: {
  cards: Card[];
  selectedCardId?: string;
  onSelect: (card: Card) => void;
  onStartRun: (card: Card) => void;
  onReviewRun: (card: Card) => void;
}) {
  return (
    <section className="panel">
      <div className="panel-header">
        <h3>Task Stream</h3>
        <span>{cards.length} cards</span>
      </div>
      <div className="panel-body card-list">
        {cards.map((card) => {
          const active = selectedCardId === card.card_id;
          if (card.card_type === "run") {
            return <RunCard key={card.card_id} card={card} active={active} onSelect={onSelect} />;
          }
          if (card.card_type === "result") {
            return <ResultCard key={card.card_id} card={card} active={active} onSelect={onSelect} />;
          }
          return (
            <ModuleCard
              key={card.card_id}
              card={card}
              active={active}
              onSelect={onSelect}
              onStartRun={onStartRun}
              onReviewRun={onReviewRun}
            />
          );
        })}
      </div>
    </section>
  );
}
