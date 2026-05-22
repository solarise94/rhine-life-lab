"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Archive, ChevronDown, ChevronUp } from "lucide-react";
import { Card, PythonRuntime, WorkOrder, WorkerCapability } from "@/lib/types";
import { ModuleCard } from "./ModuleCard";
import { ConnectionLines } from "./ConnectionLines";

export function CardStream({
  projectId,
  cards,
  workOrder,
  selectedCardId,
  onSelect,
  onClearSelection,
  onStartRun,
  onReviewRun,
  onAskManager,
  workerCapabilities = [],
  selectedWorkerByCard = {},
  onSelectWorker,
  pythonRuntimes = [],
  globalPythonRuntime,
  selectedPythonRuntimeByCard = {},
  onSelectPythonRuntime,
}: {
  projectId: string;
  cards: Card[];
  workOrder?: WorkOrder;
  selectedCardId?: string;
  onSelect: (card: Card) => void;
  onClearSelection?: () => void;
  onStartRun: (card: Card) => void;
  onReviewRun: (card: Card) => void;
  onAskManager?: (text: string) => void;
  workerCapabilities?: WorkerCapability[];
  selectedWorkerByCard?: Record<string, string | undefined>;
  onSelectWorker?: (card: Card, workerType: string) => void;
  pythonRuntimes?: PythonRuntime[];
  globalPythonRuntime?: string;
  selectedPythonRuntimeByCard?: Record<string, string | undefined>;
  onSelectPythonRuntime?: (card: Card, runtime?: string) => void;
}) {
  const moduleCards = useMemo(() => cards.filter((c) => c.card_type !== "system"), [cards]);
  const archivedCards = useMemo(
    () => moduleCards.filter((card) => card.status === "cancelled" || card.status === "rejected"),
    [moduleCards],
  );
  const activeCards = useMemo(
    () => moduleCards.filter((card) => card.status !== "cancelled" && card.status !== "rejected"),
    [moduleCards],
  );
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [rowWidths, setRowWidths] = useState<Record<string, number>>({});
  const [archiveOpen, setArchiveOpen] = useState(true);

  const orderedRows = useMemo(() => {
    const workItemStepByCard = new Map(
      workOrder?.work_items.map((item) => [item.card_id, item.step ?? 1]) ?? [],
    );
    const rowsByStep = new Map<number, Card[]>();
    for (const card of activeCards) {
      const step = card.step ?? workItemStepByCard.get(card.card_id) ?? 1;
      const row = rowsByStep.get(step) ?? [];
      row.push(card);
      rowsByStep.set(step, row);
    }
    return Array.from(rowsByStep.entries())
      .sort(([leftStep], [rightStep]) => leftStep - rightStep)
      .map(([step, rowCards]) => ({
        id: `step-${step}`,
        label: `Step ${step}`,
        cards: rowCards,
      }));
  }, [activeCards, workOrder]);

  useEffect(() => {
    const observers: ResizeObserver[] = [];

    for (const row of orderedRows) {
      const element = rowRefs.current[row.id];
      if (!element) continue;
      const updateWidth = () => {
        const nextWidth = element.clientWidth;
        setRowWidths((previous) =>
          previous[row.id] === nextWidth ? previous : { ...previous, [row.id]: nextWidth },
        );
      };
      updateWidth();
      const observer = new ResizeObserver(updateWidth);
      observer.observe(element);
      observers.push(observer);
    }

    return () => {
      observers.forEach((observer) => observer.disconnect());
    };
  }, [orderedRows, selectedCardId]);

  function shouldSpreadRow(row: { id: string; cards: Card[] }) {
    if (row.cards.length <= 1) return true;
    const availableWidth = rowWidths[row.id] ?? 0;
    if (!availableWidth) return false;
    const neededWidth =
      row.cards.reduce((sum, card) => sum + (selectedCardId === card.card_id ? 368 : 176), 0) +
      (row.cards.length - 1) * 18;
    return availableWidth >= neededWidth + 12;
  }

  return (
    <section
      className="specialist-canvas bg-grid"
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("[data-card-id]")) return;
        onClearSelection?.();
      }}
    >
      <div className="specialist-canvas-stage">
        <ConnectionLines cards={activeCards} />
        <div className="workflow-lanes">
          {orderedRows.map((row, rowIndex) => (
            <div key={row.id} className="workflow-row">
              <div className="workflow-row-label">
                <span>{row.label}</span>
                {row.cards.length > 1 ? <em>{row.cards.length} parallel specialists</em> : <em>1 specialist</em>}
              </div>
              <div
                ref={(node) => {
                  rowRefs.current[row.id] = node;
                }}
                className={`workflow-row-cards stacked-cards-group ${shouldSpreadRow(row) ? "spread-cards-group" : ""}`}
              >
                {row.cards.map((card, idx) => (
                  <div
                    key={card.card_id}
                    className="specialist-card-wrapper animate-enter"
                    style={{
                      animationDelay: `${(rowIndex * 2 + idx) * 50}ms`,
                      zIndex: selectedCardId === card.card_id ? row.cards.length + 100 : row.cards.length - idx,
                    }}
                  >
                    <ModuleCard
                      projectId={projectId}
                      card={card}
                      active={selectedCardId === card.card_id}
                      onSelect={onSelect}
                      onStartRun={onStartRun}
                      onReviewRun={onReviewRun}
                      onAskManager={onAskManager}
                      workerCapabilities={workerCapabilities}
                      selectedWorkerType={selectedWorkerByCard[card.card_id]}
                      onSelectWorker={onSelectWorker}
                      pythonRuntimes={pythonRuntimes}
                      globalPythonRuntime={globalPythonRuntime}
                      selectedPythonRuntime={selectedPythonRuntimeByCard[card.card_id]}
                      onSelectPythonRuntime={onSelectPythonRuntime}
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
          {archivedCards.length ? (
            <div className="archive-cabinet">
              <button
                type="button"
                className={`archive-drawer ${archiveOpen ? "open" : ""}`}
                onClick={() => setArchiveOpen((value) => !value)}
              >
                <span className="archive-drawer-label">
                  <Archive size={14} />
                  删除 / 归档袋
                </span>
                <span className="archive-drawer-meta">{archivedCards.length} 张已归档卡片</span>
                {archiveOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
              {archiveOpen ? (
                <div className="archive-cards-group">
                  {archivedCards.map((card, idx) => (
                    <div
                      key={card.card_id}
                      className="archive-card-wrapper animate-enter"
                      style={{
                        animationDelay: `${(orderedRows.length * 2 + idx) * 50}ms`,
                        zIndex: selectedCardId === card.card_id ? archivedCards.length + 100 : archivedCards.length - idx,
                      }}
                    >
                      <ModuleCard
                        projectId={projectId}
                        card={card}
                        active={selectedCardId === card.card_id}
                        onSelect={onSelect}
                        onStartRun={onStartRun}
                        onReviewRun={onReviewRun}
                        onAskManager={onAskManager}
                        workerCapabilities={workerCapabilities}
                        selectedWorkerType={selectedWorkerByCard[card.card_id]}
                        onSelectWorker={onSelectWorker}
                        pythonRuntimes={pythonRuntimes}
                        globalPythonRuntime={globalPythonRuntime}
                        selectedPythonRuntime={selectedPythonRuntimeByCard[card.card_id]}
                        onSelectPythonRuntime={onSelectPythonRuntime}
                      />
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          {activeCards.length === 0 && archivedCards.length === 0 ? (
            <div className="empty-state">
              暂无 specialist cards
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
