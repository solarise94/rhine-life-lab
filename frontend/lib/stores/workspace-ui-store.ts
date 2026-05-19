"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type CardPage = "specialist" | "result" | "detail" | "files" | "archive";
export type Attachment = { type: "card" | "asset"; id: string; label: string };

export const EMPTY_CARD_PAGE_BY_ID: Record<string, CardPage> = {};
export const EMPTY_ATTACHMENTS: Attachment[] = [];

interface WorkspaceUiState {
  currentChatSessionIdByProject: Record<string, string | null | undefined>;
  selectedCardByProject: Record<string, string | null | undefined>;
  noticesByProject: Record<string, string | null>;
  expandedCardByProject: Record<string, string | undefined>;
  cardPageByProject: Record<string, Record<string, CardPage>>;
  attachmentsByProject: Record<string, Attachment[]>;
  mobileTabByProject: Record<string, "chat" | "blueprint">;
  draftMessageByProject: Record<string, string>;
  setCurrentChatSessionId: (projectId: string, sessionId?: string | null) => void;
  setSelectedCard: (projectId: string, cardId?: string | null) => void;
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
      noticesByProject: {},
      expandedCardByProject: {},
      cardPageByProject: {},
      attachmentsByProject: {},
      mobileTabByProject: {},
      draftMessageByProject: {},
      setCurrentChatSessionId: (projectId, sessionId) =>
        set((state) => ({
          currentChatSessionIdByProject: {
            ...state.currentChatSessionIdByProject,
            [projectId]: sessionId,
          },
        })),
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
      setExpandedCard: (projectId, cardId) =>
        set((state) => ({
          expandedCardByProject: {
            ...state.expandedCardByProject,
            [projectId]: cardId,
          },
        })),
      setCardPage: (projectId, cardId, page) =>
        set((state) => ({
          cardPageByProject: {
            ...state.cardPageByProject,
            [projectId]: {
              ...(state.cardPageByProject[projectId] ?? {}),
              [cardId]: page,
            },
          },
        })),
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
        set((state) => ({
          mobileTabByProject: {
            ...state.mobileTabByProject,
            [projectId]: tab,
          },
        })),
      setDraftMessage: (projectId, message) =>
        set((state) => ({
          draftMessageByProject: {
            ...state.draftMessageByProject,
            [projectId]: message,
          },
        })),
      clearDraftMessage: (projectId) =>
        set((state) => ({
          draftMessageByProject: {
            ...state.draftMessageByProject,
            [projectId]: "",
          },
        })),
    }),
    {
      name: "blueprint-workspace-ui-v2",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        currentChatSessionIdByProject: state.currentChatSessionIdByProject,
        selectedCardByProject: state.selectedCardByProject,
        expandedCardByProject: state.expandedCardByProject,
        cardPageByProject: state.cardPageByProject,
        attachmentsByProject: state.attachmentsByProject,
        mobileTabByProject: state.mobileTabByProject,
        draftMessageByProject: state.draftMessageByProject,
      }),
    },
  ),
);
