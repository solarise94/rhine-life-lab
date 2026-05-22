"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type CardPage = "specialist" | "result" | "detail" | "files" | "archive";
export type Attachment = { type: "card" | "asset"; id: string; label: string };

export const EMPTY_CARD_PAGE_BY_ID: Record<string, CardPage> = {};
export const EMPTY_ATTACHMENTS: Attachment[] = [];
export const EMPTY_SELECTED_WORKER_BY_CARD: Record<string, string | undefined> = {};
export const EMPTY_SELECTED_RUNTIME_BY_CARD: Record<string, string | undefined> = {};

interface WorkspaceUiState {
  currentChatSessionIdByProject: Record<string, string | null | undefined>;
  selectedCardByProject: Record<string, string | null | undefined>;
  selectedWorkerByProject: Record<string, Record<string, string | undefined>>;
  globalPythonRuntimeByProject: Record<string, string | undefined>;
  selectedPythonRuntimeByProject: Record<string, Record<string, string | undefined>>;
  noticesByProject: Record<string, string | null>;
  expandedCardByProject: Record<string, string | undefined>;
  cardPageByProject: Record<string, Record<string, CardPage>>;
  attachmentsByProject: Record<string, Attachment[]>;
  mobileTabByProject: Record<string, "chat" | "blueprint">;
  draftMessageByProject: Record<string, string>;
  setCurrentChatSessionId: (projectId: string, sessionId?: string | null) => void;
  setSelectedCard: (projectId: string, cardId?: string | null) => void;
  setSelectedWorker: (projectId: string, cardId: string, workerType?: string) => void;
  setGlobalPythonRuntime: (projectId: string, runtime?: string) => void;
  setSelectedPythonRuntime: (projectId: string, cardId: string, runtime?: string) => void;
  setNotice: (projectId: string, message: string | null) => void;
  setExpandedCard: (projectId: string, cardId?: string) => void;
  setCardPage: (projectId: string, cardId: string, page: CardPage) => void;
  addAttachment: (projectId: string, attachment: Attachment) => void;
  removeAttachment: (projectId: string, id: string) => void;
  clearAttachments: (projectId: string) => void;
  setMobileTab: (projectId: string, tab: "chat" | "blueprint") => void;
  setDraftMessage: (projectId: string, message: string) => void;
  clearDraftMessage: (projectId: string) => void;
}

export const useWorkspaceUiStore = create<WorkspaceUiState>()(
  persist(
    (set) => ({
      currentChatSessionIdByProject: {},
      selectedCardByProject: {},
      selectedWorkerByProject: {},
      globalPythonRuntimeByProject: {},
      selectedPythonRuntimeByProject: {},
      noticesByProject: {},
      expandedCardByProject: {},
      cardPageByProject: {},
      attachmentsByProject: {},
      mobileTabByProject: {},
      draftMessageByProject: {},
      setCurrentChatSessionId: (projectId, sessionId) =>
        set((state) => {
          if (state.currentChatSessionIdByProject[projectId] === sessionId) return state;
          return {
            currentChatSessionIdByProject: {
              ...state.currentChatSessionIdByProject,
              [projectId]: sessionId,
            },
          };
        }),
      setSelectedCard: (projectId, cardId) =>
        set((state) => {
          if (state.selectedCardByProject[projectId] === cardId) return state;
          return {
            selectedCardByProject: {
              ...state.selectedCardByProject,
              [projectId]: cardId,
            },
          };
        }),
      setSelectedWorker: (projectId, cardId, workerType) =>
        set((state) => {
          if (state.selectedWorkerByProject[projectId]?.[cardId] === workerType) return state;
          return {
            selectedWorkerByProject: {
              ...state.selectedWorkerByProject,
              [projectId]: {
                ...(state.selectedWorkerByProject[projectId] ?? {}),
                [cardId]: workerType,
              },
            },
          };
        }),
      setGlobalPythonRuntime: (projectId, runtime) =>
        set((state) => {
          if (state.globalPythonRuntimeByProject[projectId] === runtime) return state;
          return {
            globalPythonRuntimeByProject: {
              ...state.globalPythonRuntimeByProject,
              [projectId]: runtime,
            },
          };
        }),
      setSelectedPythonRuntime: (projectId, cardId, runtime) =>
        set((state) => {
          if (state.selectedPythonRuntimeByProject[projectId]?.[cardId] === runtime) return state;
          return {
            selectedPythonRuntimeByProject: {
              ...state.selectedPythonRuntimeByProject,
              [projectId]: {
                ...(state.selectedPythonRuntimeByProject[projectId] ?? {}),
                [cardId]: runtime,
              },
            },
          };
        }),
      setNotice: (projectId, message) =>
        set((state) => {
          if (state.noticesByProject[projectId] === message) return state;
          return {
            noticesByProject: {
              ...state.noticesByProject,
              [projectId]: message,
            },
          };
        }),
      setExpandedCard: (projectId, cardId) =>
        set((state) => ({
          expandedCardByProject: {
            ...state.expandedCardByProject,
            [projectId]: cardId,
          },
        })),
      setCardPage: (projectId, cardId, page) =>
        set((state) => {
          if (state.cardPageByProject[projectId]?.[cardId] === page) return state;
          return {
            cardPageByProject: {
              ...state.cardPageByProject,
              [projectId]: {
                ...(state.cardPageByProject[projectId] ?? {}),
                [cardId]: page,
              },
            },
          };
        }),
      addAttachment: (projectId, attachment) =>
        set((state) => {
          const list = state.attachmentsByProject[projectId] ?? [];
          if (list.some((a) => a.id === attachment.id)) return state;
          return {
            attachmentsByProject: {
              ...state.attachmentsByProject,
              [projectId]: [...list, attachment],
            },
          };
        }),
      removeAttachment: (projectId, id) =>
        set((state) => ({
          attachmentsByProject: {
            ...state.attachmentsByProject,
            [projectId]: (state.attachmentsByProject[projectId] ?? []).filter((a) => a.id !== id),
          },
        })),
      clearAttachments: (projectId) =>
        set((state) => ({
          attachmentsByProject: {
            ...state.attachmentsByProject,
            [projectId]: [],
          },
        })),
      setMobileTab: (projectId, tab) =>
        set((state) => {
          if (state.mobileTabByProject[projectId] === tab) return state;
          return {
            mobileTabByProject: {
              ...state.mobileTabByProject,
              [projectId]: tab,
            },
          };
        }),
      setDraftMessage: (projectId, message) =>
        set((state) => {
          if (state.draftMessageByProject[projectId] === message) return state;
          return {
            draftMessageByProject: {
              ...state.draftMessageByProject,
              [projectId]: message,
            },
          };
        }),
      clearDraftMessage: (projectId) =>
        set((state) => ({
          draftMessageByProject: {
            ...state.draftMessageByProject,
            [projectId]: "",
          },
        })),
    }),
    {
      name: "blueprint-workspace-ui-v3",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        currentChatSessionIdByProject: state.currentChatSessionIdByProject,
        selectedCardByProject: state.selectedCardByProject,
        selectedWorkerByProject: state.selectedWorkerByProject,
        globalPythonRuntimeByProject: state.globalPythonRuntimeByProject,
        selectedPythonRuntimeByProject: state.selectedPythonRuntimeByProject,
        expandedCardByProject: state.expandedCardByProject,
        cardPageByProject: state.cardPageByProject,
        attachmentsByProject: state.attachmentsByProject,
        mobileTabByProject: state.mobileTabByProject,
        draftMessageByProject: state.draftMessageByProject,
      }),
    },
  ),
);
