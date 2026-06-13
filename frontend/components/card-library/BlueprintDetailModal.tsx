"use client";

import { useEffect } from "react";
import { X } from "lucide-react";

interface BlueprintDetailModalProps {
  open: boolean;
  title?: string;
  onClose: () => void;
  children: React.ReactNode;
  actions?: React.ReactNode;
}

export function BlueprintDetailModal({ open, title, onClose, children, actions }: BlueprintDetailModalProps) {
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="blueprint-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
    >
      <div className="blueprint-modal">
        <div className="blueprint-modal-header">
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{title || "详情"}</h3>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {actions ? <div style={{ display: "flex", gap: 8 }}>{actions}</div> : null}
            <button
              type="button"
              className="btn secondary"
              style={{ padding: "6px" }}
              onClick={onClose}
              aria-label="关闭"
              title="关闭"
            >
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="blueprint-modal-body">{children}</div>
      </div>
    </div>
  );
}
