"use client";

import { create } from "zustand";

interface ReportViewState {
  selectedSectionByProject: Record<string, string | undefined>;
  setSelectedSection: (projectId: string, sectionId?: string) => void;
}

export const useReportViewStore = create<ReportViewState>((set) => ({
  selectedSectionByProject: {},
  setSelectedSection: (projectId, sectionId) =>
    set((state) => ({
      selectedSectionByProject: {
        ...state.selectedSectionByProject,
        [projectId]: sectionId,
      },
    })),
}));
