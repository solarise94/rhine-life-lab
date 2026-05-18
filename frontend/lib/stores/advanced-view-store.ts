"use client";

import { create } from "zustand";

type AdvancedDocument = "graph" | "proposals";

interface AdvancedViewState {
  activeDocumentByProject: Record<string, AdvancedDocument>;
  setActiveDocument: (projectId: string, document: AdvancedDocument) => void;
}

export const useAdvancedViewStore = create<AdvancedViewState>((set) => ({
  activeDocumentByProject: {},
  setActiveDocument: (projectId, document) =>
    set((state) => ({
      activeDocumentByProject: {
        ...state.activeDocumentByProject,
        [projectId]: document,
      },
    })),
}));
