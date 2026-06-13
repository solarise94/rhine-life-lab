"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

function computeExpandedRect(origin: Rect): Rect {
  if (typeof window === "undefined") return origin;
  const maxWidth = Math.min(760, window.innerWidth - 32);
  const maxHeight = Math.min(720, window.innerHeight - 80);
  const width = Math.max(origin.width, maxWidth);
  const height = Math.max(origin.height, maxHeight);
  const top = Math.max(24, (window.innerHeight - height) / 2);
  const left = (window.innerWidth - width) / 2;
  return { top, left, width, height };
}

interface BlueprintExpandingCardProps {
  open: boolean;
  originRect: Rect | null;
  title?: string;
  onClose: () => void;
  children: React.ReactNode;
  actions?: React.ReactNode;
}

export function BlueprintExpandingCard({
  open,
  originRect,
  title,
  onClose,
  children,
  actions,
}: BlueprintExpandingCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState<"hidden" | "entering" | "entered" | "exiting">("hidden");
  const [currentRect, setCurrentRect] = useState<Rect | null>(null);

  useEffect(() => {
    if (!open) return;
    if (!originRect) {
      setCurrentRect(computeExpandedRect({ top: 0, left: 0, width: 0, height: 0 }));
      setPhase("entered");
      return;
    }
    setCurrentRect(originRect);
    setPhase("entering");
    const raf = requestAnimationFrame(() => {
      setCurrentRect(computeExpandedRect(originRect));
      setPhase("entered");
    });
    return () => cancelAnimationFrame(raf);
  }, [open, originRect]);

  useEffect(() => {
    if (phase !== "entered") return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") handleClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [phase]);

  function handleClose() {
    if (phase === "exiting") return;
    setPhase("exiting");
    if (originRect) {
      setCurrentRect(originRect);
    }
    setTimeout(() => {
      onClose();
    }, 280);
  }

  if (!open || !currentRect) return null;

  const isAnimating = phase === "entering" || phase === "exiting";

  return (
    <div
      className="blueprint-expand-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        ref={cardRef}
        className={`blueprint-expand-card ${phase}`}
        style={{
          position: "fixed",
          top: currentRect.top,
          left: currentRect.left,
          width: currentRect.width,
          height: currentRect.height,
          transition: isAnimating
            ? "top 0.28s cubic-bezier(0.2, 0, 0.2, 1), left 0.28s cubic-bezier(0.2, 0, 0.2, 1), width 0.28s cubic-bezier(0.2, 0, 0.2, 1), height 0.28s cubic-bezier(0.2, 0, 0.2, 1), border-radius 0.28s ease"
            : undefined,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="blueprint-expand-header">
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{title || "详情"}</h3>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {actions ? <div style={{ display: "flex", gap: 8 }}>{actions}</div> : null}
            <button
              type="button"
              className="btn secondary"
              style={{ padding: "6px" }}
              onClick={handleClose}
              aria-label="关闭"
              title="关闭"
            >
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="blueprint-expand-body">{children}</div>
      </div>
    </div>
  );
}
