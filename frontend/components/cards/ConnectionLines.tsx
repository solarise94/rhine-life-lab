"use client";

import { useEffect, useRef, useState } from "react";
import { Card } from "@/lib/types";

interface Line {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  label: string;
}

export function ConnectionLines({ cards }: { cards: Card[] }) {
  const containerRef = useRef<SVGSVGElement>(null);
  const [lines, setLines] = useState<Line[]>([]);

  useEffect(() => {
    function getAnchorCenter(
      container: Element,
      containerRect: DOMRect,
      anchorId: string,
    ): { x: number; y: number } | null {
      const anchor = container.querySelector<HTMLElement>(`[data-anchor="${anchorId}"]`);
      if (!anchor) return null;
      const rect = anchor.getBoundingClientRect();
      return {
        x: rect.left + rect.width / 2 - containerRect.left,
        y: rect.top + rect.height / 2 - containerRect.top,
      };
    }

    let rafId = 0;
    function updateLines() {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const container = containerRef.current?.parentElement;
        if (!container) return;

        const containerRect = container.getBoundingClientRect();
        const newLines: Line[] = [];

        for (const card of cards) {
          const fromPoint = getAnchorCenter(container, containerRect, `out-${card.card_id}`);
          if (!fromPoint) continue;

          for (const output of card.outputs) {
            if (!output.asset_id) continue;
            for (const target of cards) {
              if (target.card_id === card.card_id) continue;
              const hasInput = target.inputs.some((i) => i.asset_id === output.asset_id);
              if (!hasInput) continue;
              const toPoint = getAnchorCenter(container, containerRect, `in-${target.card_id}`);
              if (!toPoint) continue;

              newLines.push({
                id: `${card.card_id}-${target.card_id}-${output.asset_id}`,
                x1: fromPoint.x,
                y1: fromPoint.y,
                x2: toPoint.x,
                y2: toPoint.y,
                label: output.label,
              });
            }
          }
        }

        setLines(newLines);
      });
    }

    updateLines();

    const ro = new ResizeObserver(() => {
      updateLines();
    });
    const parent = containerRef.current?.parentElement;
    if (parent) {
      ro.observe(parent);
    }

    window.addEventListener("resize", updateLines);

    // Scroll inside the canvas or workflow rows changes anchor positions
    const scrollTargets = parent ? Array.from(parent.querySelectorAll(".specialist-canvas, .workflow-row-cards")) : [];
    scrollTargets.forEach((el) => el.addEventListener("scroll", updateLines, { passive: true }));

    // Observe DOM mutations inside the canvas (cards added/removed/attributes changed)
    const mo = new MutationObserver(() => {
      updateLines();
    });
    if (parent) {
      mo.observe(parent, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"] });
    }

    // Re-calculate when any CSS transition inside the canvas ends
    function onTransitionEnd() {
      updateLines();
    }
    parent?.addEventListener("transitionend", onTransitionEnd);

    return () => {
      ro.disconnect();
      mo.disconnect();
      window.removeEventListener("resize", updateLines);
      scrollTargets.forEach((el) => el.removeEventListener("scroll", updateLines));
      parent?.removeEventListener("transitionend", onTransitionEnd);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [cards]);

  return (
    <svg ref={containerRef} className="connection-layer" width="100%" height="100%">
      {lines.map((line) => {
        const verticalGap = Math.max(22, Math.abs(line.y2 - line.y1) * 0.45);
        const d = `M ${line.x1} ${line.y1} C ${line.x1} ${line.y1 + verticalGap}, ${line.x2} ${line.y2 - verticalGap}, ${line.x2} ${line.y2}`;
        return (
          <g key={line.id}>
            <path
              className="connection-line"
              d={d}
              strokeLinecap="round"
            >
              <title>{line.label}</title>
            </path>
            <circle className="connection-dot" cx={line.x2} cy={line.y2} r="3.5">
              <title>{line.label}</title>
            </circle>
          </g>
        );
      })}
    </svg>
  );
}
