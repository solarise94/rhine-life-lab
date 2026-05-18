"use client";

import { create } from "zustand";

interface ResultsViewState {
  selectedAssetByProject: Record<string, string | undefined>;
  setSelectedAsset: (projectId: string, assetId?: string) => void;
}

export const useResultsViewStore = create<ResultsViewState>((set) => ({
  selectedAssetByProject: {},
  setSelectedAsset: (projectId, assetId) =>
    set((state) => ({
      selectedAssetByProject: {
        ...state.selectedAssetByProject,
        [projectId]: assetId,
      },
    })),
}));
