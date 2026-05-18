"use client";

import { create } from "zustand";

interface WorkspaceUiState {
  selectedCardByProject: Record<string, string | undefined>;
  noticesByProject: Record<string, string | null>;
  setSelectedCard: (projectId: string, cardId?: string) => void;
  setNotice: (projectId: string, message: string | null) => void;
}

export const useWorkspaceUiStore = create<WorkspaceUiState>((set) => ({
  selectedCardByProject: {},
  noticesByProject: {},
  setSelectedCard: (projectId, cardId) =>
    set((state) => ({
      selectedCardByProject: {
        ...state.selectedCardByProject,
        [projectId]: cardId,
      },
    })),
  setNotice: (projectId, message) =>
    set((state) => ({
      noticesByProject: {
        ...state.noticesByProject,
        [projectId]: message,
      },
    })),
}));
