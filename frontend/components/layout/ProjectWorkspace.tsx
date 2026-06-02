"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { ApiError, api, apiUrl } from "@/lib/api";
import {
  useAdvancedGit,
  useAdvancedGraph,
  useExecutorProfiles,
  useManagerAuto,
  useProjectFiles,
  useProjectReport,
  useProjectResults,
  useProjectSnapshot,
  useReportExportMutation,
  useReportReorderMutation,
  useResultAsset,
  useReviewRunMutation,
  useStartRunMutation,
  useUpdateProjectRuntimePreferencesMutation,
  useWorkOrder,
  useWorkspaceRefresh,
} from "@/lib/hooks";
import { queryKeys } from "@/lib/query-keys";
import { useReportViewStore } from "@/lib/stores/report-view-store";
import { useResultsViewStore } from "@/lib/stores/results-view-store";
import {
  EMPTY_ARTIFACT_PREVIEW_STATE,
  EMPTY_SELECTED_RUNTIME_BY_CARD,
  EMPTY_SELECTED_WORKER_BY_CARD,
  EMPTY_SELECTED_PROFILE_BY_CARD,
  useWorkspaceUiStore,
} from "@/lib/stores/workspace-ui-store";
import { Card, ExecutorProfile, ReportExportResponse } from "@/lib/types";
import { SideNav } from "./SideNav";
import { ProjectHeader } from "./ProjectHeader";
import { ManagerChatPanel } from "@/components/manager-chat/ManagerChatPanel";
import { CardStream } from "@/components/cards/CardStream";
import { CardDetailPanel } from "@/components/detail/CardDetailPanel";
import { ResultsGrid } from "@/components/results/ResultsGrid";
import { ReportBuilder } from "@/components/report/ReportBuilder";
import { FilesPanel } from "@/components/files/FilesPanel";

const ResultsOverviewChart = dynamic(
  () => import("@/components/results/ResultsOverviewChart").then((m) => m.ResultsOverviewChart),
  { ssr: false },
);
const ResultPreviewPanel = dynamic(
  () => import("@/components/results/ResultPreviewPanel").then((m) => m.ResultPreviewPanel),
  { ssr: false },
);
const ReportSectionDetailPanel = dynamic(
  () => import("@/components/report/ReportSectionDetailPanel").then((m) => m.ReportSectionDetailPanel),
  { ssr: false },
);
const AdvancedPanels = dynamic(
  () => import("@/components/advanced/AdvancedPanels").then((m) => m.AdvancedPanels),
  { ssr: false },
);
const SettingsPanels = dynamic(
  () => import("@/components/settings/SettingsPanels").then((m) => m.SettingsPanels),
  { ssr: false },
);

type View = "tasks" | "results" | "files" | "report" | "advanced" | "settings";
const EMPTY_CARD_INTERACTION_ORDER: string[] = [];

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia(query).matches,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia(query);
    const handleChange = () => setMatches(media.matches);
    handleChange();
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [query]);

  return matches;
}

function formatRuntime(runtime?: string) {
  if (!runtime || runtime === "__system__") return "system";
  return runtime;
}

function preferredExecutorProfile(profiles: ExecutorProfile[], workerType?: string) {
  if (!workerType) return profiles[0];
  const candidates = workerType ? profiles.filter((profile) => profile.worker_type === workerType) : profiles;
  const preferredAuthMode = workerType === "pi" || workerType === "opencode" ? "project_api" : "cli_native";
  return candidates.find((profile) => profile.auth_mode === preferredAuthMode) ?? candidates[0];
}

export function ProjectWorkspace({ projectId, view }: { projectId: string; view: View }) {
  const queryClient = useQueryClient();
  const isMobileWorkspace = useMediaQuery("(max-width: 1100px)");
  const projectEventSourceRef = useRef<EventSource | null>(null);
  const projectEventReconnectTimerRef = useRef<number | null>(null);
  const projectRefreshTimerRef = useRef<number | null>(null);
  const projectDelayedRefreshTimerRef = useRef<number | null>(null);
  const currentChatSessionIdRef = useRef<string | null>(null);
  const noticeRef = useRef<string | null>(null);
  const selectedCardId = useWorkspaceUiStore((s) => s.selectedCardByProject[projectId]);
  const cardInteractionOrder = useWorkspaceUiStore(
    (s) => s.cardInteractionOrderByProject[projectId] ?? EMPTY_CARD_INTERACTION_ORDER,
  );
  const selectedWorkerByProject = useWorkspaceUiStore((s) => s.selectedWorkerByProject[projectId] ?? EMPTY_SELECTED_WORKER_BY_CARD);
  const selectedProfileByProject = useWorkspaceUiStore((s) => s.selectedProfileByProject[projectId] ?? EMPTY_SELECTED_PROFILE_BY_CARD);
  const globalPythonRuntime = useWorkspaceUiStore((s) => s.globalPythonRuntimeByProject?.[projectId]);
  const selectedPythonRuntimeByProject = useWorkspaceUiStore((s) => s.selectedPythonRuntimeByProject?.[projectId] ?? EMPTY_SELECTED_RUNTIME_BY_CARD);
  const globalRRuntime = useWorkspaceUiStore((s) => s.globalRRuntimeByProject?.[projectId]);
  const selectedRRuntimeByProject = useWorkspaceUiStore((s) => s.selectedRRuntimeByProject?.[projectId] ?? EMPTY_SELECTED_RUNTIME_BY_CARD);
  const scriptPreference = useWorkspaceUiStore((s) => s.scriptPreferenceByProject?.[projectId] ?? "auto");
  const currentChatSessionId = useWorkspaceUiStore((s) => s.currentChatSessionIdByProject[projectId] ?? null);
  currentChatSessionIdRef.current = currentChatSessionId;
  const notice = useWorkspaceUiStore((s) => s.noticesByProject[projectId] ?? null);
  noticeRef.current = notice;
  const setSelectedCard = useWorkspaceUiStore((s) => s.setSelectedCard);
  const setSelectedWorker = useWorkspaceUiStore((s) => s.setSelectedWorker);
  const setSelectedProfile = useWorkspaceUiStore((s) => s.setSelectedProfile);
  const setGlobalPythonRuntime = useWorkspaceUiStore((s) => s.setGlobalPythonRuntime);
  const setSelectedPythonRuntime = useWorkspaceUiStore((s) => s.setSelectedPythonRuntime);
  const setGlobalRRuntime = useWorkspaceUiStore((s) => s.setGlobalRRuntime);
  const setSelectedRRuntime = useWorkspaceUiStore((s) => s.setSelectedRRuntime);
  const setScriptPreference = useWorkspaceUiStore((s) => s.setScriptPreference);
  const setNotice = useWorkspaceUiStore((s) => s.setNotice);
  const mobileTab = useWorkspaceUiStore((s) => s.mobileTabByProject[projectId] ?? "chat");
  const setMobileTab = useWorkspaceUiStore((s) => s.setMobileTab);
  const artifactPreview = useWorkspaceUiStore((s) => s.artifactPreviewByProject[projectId] ?? EMPTY_ARTIFACT_PREVIEW_STATE);
  const openArtifactPreview = useWorkspaceUiStore((s) => s.openArtifactPreview);
  const closeArtifactPreview = useWorkspaceUiStore((s) => s.closeArtifactPreview);
  const setArtifactPreviewLoading = useWorkspaceUiStore((s) => s.setArtifactPreviewLoading);
  const setArtifactPreviewError = useWorkspaceUiStore((s) => s.setArtifactPreviewError);
  const addAttachment = useWorkspaceUiStore((s) => s.addAttachment);
  const setDraftMessage = useWorkspaceUiStore((s) => s.setDraftMessage);

  const selectedAssetId = useResultsViewStore((s) => s.selectedAssetByProject[projectId]);
  const setSelectedAsset = useResultsViewStore((s) => s.setSelectedAsset);
  const selectedSectionId = useReportViewStore((s) => s.selectedSectionByProject[projectId]);
  const setSelectedSection = useReportViewStore((s) => s.setSelectedSection);
  const refreshWorkspace = useWorkspaceRefresh(projectId);

  const projectQuery = useProjectSnapshot(projectId);
  const managerAutoQuery = useManagerAuto(projectId, currentChatSessionId);
  const workOrderQuery = useWorkOrder(projectId, view === "tasks");
  const resultsQuery = useProjectResults(projectId, view === "results");
  const filesQuery = useProjectFiles(projectId, view === "files");
  const reportQuery = useProjectReport(projectId, view === "report");
  const advancedGraphQuery = useAdvancedGraph(projectId, view === "advanced");
  const advancedGitQuery = useAdvancedGit(projectId, view === "advanced");
  const startRunMutation = useStartRunMutation(projectId);
  const reviewRunMutation = useReviewRunMutation(projectId);
  const updateProjectRuntimePreferencesMutation = useUpdateProjectRuntimePreferencesMutation(projectId);
  const reorderReportMutation = useReportReorderMutation(projectId);
  const exportReportMutation = useReportExportMutation(projectId);
  const executorProfilesQuery = useExecutorProfiles();
  const executorProfiles = executorProfilesQuery.data?.profiles ?? [];
  const [lastReportExport, setLastReportExport] = useState<ReportExportResponse | null>(null);

  const snapshot = projectQuery.data;
  const managerAuto = managerAutoQuery.data?.state ?? snapshot?.manager_auto;
  const autoEnabled = Boolean(managerAuto?.enabled);
  const autoOwnerSessionId = managerAuto?.owner_session_id ?? null;
  const autoLocked = autoEnabled;
  const projectRuntimePreferences = snapshot?.project.runtime_preferences;
  const defaultTaskCard = useMemo(
    () =>
      snapshot?.cards.find((item) => item.status !== "cancelled" && item.status !== "rejected") ??
      snapshot?.cards[0],
    [snapshot],
  );
  const selectedCard =
    selectedCardId === null
      ? undefined
      : snapshot?.cards.find((item) => item.card_id === selectedCardId) ?? defaultTaskCard;
  const selectedRunId = selectedCard?.linked_runs.at(-1);
  const selectedRun = snapshot?.graph.runs.find((item) => item.run_id === selectedRunId);
  const selectedWorkItem = workOrderQuery.data?.work_items.find((item) => item.card_id === selectedCard?.card_id);
  const configuredWorkers = useMemo(
    () => (snapshot?.worker_capabilities ?? []).filter((item) => item.configured),
    [snapshot?.worker_capabilities],
  );
  const activeRunCount = useMemo(
    () => snapshot?.graph.runs.filter((item) => ["queued", "needs_approval", "running", "reviewing"].includes(item.status)).length ?? 0,
    [snapshot?.graph.runs],
  );

  function refetchProjectEventState(runId?: string | null) {
    const queries = [
      queryClient.refetchQueries({ queryKey: queryKeys.project(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.workOrder(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.advancedProposals(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.results(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.files(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.managerAuto(projectId, currentChatSessionIdRef.current), type: "active" }),
    ];
    if (runId) {
      queries.push(queryClient.refetchQueries({ queryKey: queryKeys.runEvents(projectId, runId), type: "active" }));
    }
    void Promise.all(queries);
  }

  function scheduleProjectEventRefresh(runId?: string | null) {
    if (typeof window === "undefined") return;
    if (projectRefreshTimerRef.current !== null) {
      window.clearTimeout(projectRefreshTimerRef.current);
    }
    projectRefreshTimerRef.current = window.setTimeout(() => {
      projectRefreshTimerRef.current = null;
      refetchProjectEventState(runId);
    }, 120);
    if (projectDelayedRefreshTimerRef.current !== null) {
      window.clearTimeout(projectDelayedRefreshTimerRef.current);
    }
    projectDelayedRefreshTimerRef.current = window.setTimeout(() => {
      projectDelayedRefreshTimerRef.current = null;
      refetchProjectEventState(runId);
    }, 1_000);
  }

  useEffect(() => {
    projectEventSourceRef.current?.close();
    projectEventSourceRef.current = null;
    if (projectEventReconnectTimerRef.current !== null && typeof window !== "undefined") {
      window.clearTimeout(projectEventReconnectTimerRef.current);
      projectEventReconnectTimerRef.current = null;
    }
    if (typeof window === "undefined") {
      return;
    }
    let stopped = false;
    let reconnectAttempt = 0;

    const connect = () => {
      if (stopped) return;
      const source = new EventSource(apiUrl(`/projects/${projectId}/events`));
      projectEventSourceRef.current = source;
      source.onopen = () => {
        reconnectAttempt = 0;
      };
      source.onmessage = (event) => {
        if (!event.data) return;
        const raw = JSON.parse(event.data) as {
          type?: string;
          reason?: string;
          run_id?: string | null;
          job_status?: string;
          payload?: {
            requested_package?: string;
            fallback_available?: string[];
            message?: string;
            card_id?: string;
            resolution_status?: string;
            error_code?: string;
          };
        };
        if (raw.type === "heartbeat") {
          return;
        }
        // Surface failed dependency install events as a project-level notice.
        // The enriched failure fields live inside the nested payload object.
        if (
          raw.reason === "runtime_dependency_job_changed" &&
          raw.job_status === "failed"
        ) {
          const eventPayload = raw.payload || {};
          // Ignore manually resolved jobs to avoid re-showing the failure notice.
          if (eventPayload.resolution_status === "manually_resolved") {
            // Clear any existing dependency failure notice so it disappears immediately.
            // Read from noticeRef to avoid stale closure value.
            const currentNotice = noticeRef.current;
            if (currentNotice && currentNotice.startsWith("Dependency install failed")) {
              setNotice(projectId, null);
            }
          } else {
            const pkg = eventPayload.requested_package || "unknown package";
            const fallback = eventPayload.fallback_available?.length
              ? `Fallback available: ${eventPayload.fallback_available.join(", ")}.`
              : "";
            const msg = eventPayload.message || "";
            const errorCode = eventPayload.error_code;
            let noticeText: string;
            if (errorCode === "package_not_found_in_conda_channels") {
              noticeText = `Dependency install failed: ${pkg} was not found in configured conda channels. ${fallback} ${msg}`.trim();
            } else {
              // Use the backend message directly for timeout, start_failed, compilation_failed, etc.
              noticeText = `Dependency install failed: ${msg || pkg}`.trim();
            }
            setNotice(projectId, noticeText);
          }
        }
        const runId = typeof raw.run_id === "string" ? raw.run_id : null;
        scheduleProjectEventRefresh(runId);
      };
      source.onerror = () => {
        source.close();
        if (projectEventSourceRef.current === source) {
          projectEventSourceRef.current = null;
        }
        if (stopped) return;
        refetchProjectEventState();
        const delay = Math.min(10_000, 1_000 * 2 ** reconnectAttempt);
        reconnectAttempt += 1;
        projectEventReconnectTimerRef.current = window.setTimeout(() => {
          projectEventReconnectTimerRef.current = null;
          connect();
        }, delay);
      };
    };

    connect();
    return () => {
      stopped = true;
      projectEventSourceRef.current?.close();
      projectEventSourceRef.current = null;
      if (projectEventReconnectTimerRef.current !== null) {
        window.clearTimeout(projectEventReconnectTimerRef.current);
        projectEventReconnectTimerRef.current = null;
      }
      if (projectRefreshTimerRef.current !== null) {
        window.clearTimeout(projectRefreshTimerRef.current);
        projectRefreshTimerRef.current = null;
      }
      if (projectDelayedRefreshTimerRef.current !== null) {
        window.clearTimeout(projectDelayedRefreshTimerRef.current);
        projectDelayedRefreshTimerRef.current = null;
      }
    };
  }, [projectId, queryClient]);
  const selectedWorkerType = selectedCard
    ? selectedWorkerByProject[selectedCard.card_id] ?? selectedRun?.worker_type ?? configuredWorkers[0]?.worker_type
    : configuredWorkers[0]?.worker_type;
  const selectedPythonRuntime = selectedCard ? selectedPythonRuntimeByProject[selectedCard.card_id] : undefined;
  const selectedRRuntime = selectedCard ? selectedRRuntimeByProject[selectedCard.card_id] : undefined;
  const allResultAssets = useMemo(
    () => (resultsQuery.data ? [...resultsQuery.data.accepted, ...resultsQuery.data.candidate, ...resultsQuery.data.other] : []),
    [resultsQuery.data],
  );
  const selectedAsset = allResultAssets.find((item) => item.asset_id === selectedAssetId) ?? allResultAssets[0];
  const selectedSection = reportQuery.data?.sections.find((item) => item.item_id === selectedSectionId) ?? reportQuery.data?.sections[0];
  const effectiveGlobalPythonRuntime = globalPythonRuntime ?? projectRuntimePreferences?.python_runtime ?? undefined;
  const effectiveGlobalRRuntime = globalRRuntime ?? projectRuntimePreferences?.r_runtime ?? undefined;
  const effectiveScriptPreference = scriptPreference ?? projectRuntimePreferences?.script_preference ?? "auto";

  const previewAssetId = artifactPreview.source?.assetId;
  const resultAssetQuery = useResultAsset(projectId, previewAssetId, artifactPreview.open && Boolean(previewAssetId));

  useEffect(() => {
    if (!defaultTaskCard || selectedCardId !== undefined) return;
    setSelectedCard(projectId, defaultTaskCard.card_id);
  }, [defaultTaskCard, projectId, selectedCardId, setSelectedCard]);

  useEffect(() => {
    if (!selectedCard || !configuredWorkers.length) return;
    const current = selectedWorkerByProject[selectedCard.card_id];
    if (current && configuredWorkers.some((item) => item.worker_type === current)) return;
    const fallback = selectedRun?.worker_type && configuredWorkers.some((item) => item.worker_type === selectedRun.worker_type)
      ? selectedRun.worker_type
      : configuredWorkers[0]?.worker_type;
    if (fallback) {
      setSelectedWorker(projectId, selectedCard.card_id, fallback);
    }
  }, [configuredWorkers, projectId, selectedCard, selectedRun?.worker_type, selectedWorkerByProject, setSelectedWorker]);

  useEffect(() => {
    if (!projectRuntimePreferences) return;
    if (effectiveGlobalPythonRuntime !== (projectRuntimePreferences.python_runtime ?? undefined)) {
      setGlobalPythonRuntime(projectId, projectRuntimePreferences.python_runtime ?? undefined);
    }
    if (effectiveGlobalRRuntime !== (projectRuntimePreferences.r_runtime ?? undefined)) {
      setGlobalRRuntime(projectId, projectRuntimePreferences.r_runtime ?? undefined);
    }
    if (effectiveScriptPreference !== projectRuntimePreferences.script_preference) {
      setScriptPreference(projectId, projectRuntimePreferences.script_preference);
    }
  }, [
    effectiveGlobalPythonRuntime,
    effectiveGlobalRRuntime,
    effectiveScriptPreference,
    projectId,
    projectRuntimePreferences,
    setGlobalPythonRuntime,
    setGlobalRRuntime,
    setScriptPreference,
  ]);

  useEffect(() => {
    if (view !== "results" || !allResultAssets.length || selectedAssetId) return;
    setSelectedAsset(projectId, allResultAssets[0].asset_id);
  }, [allResultAssets, projectId, selectedAssetId, setSelectedAsset, view]);

  useEffect(() => {
    if (view !== "report" || !reportQuery.data?.sections.length || selectedSectionId) return;
    setSelectedSection(projectId, reportQuery.data.sections[0].item_id);
  }, [projectId, reportQuery.data, selectedSectionId, setSelectedSection, view]);

  useEffect(() => {
    setLastReportExport(null);
  }, [projectId]);

  useEffect(() => {
    if (view !== "tasks" || activeRunCount === 0) return;
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.workOrder(projectId) });
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [activeRunCount, projectId, queryClient, view]);

  useEffect(() => {
    if (!notice) return;
    // Keep dependency failure notices visible longer so users can read them.
    const delay = notice.startsWith("Dependency install failed") ? 30_000 : 2_200;
    const timer = window.setTimeout(() => {
      setNotice(projectId, null);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [notice, projectId, setNotice]);

  useEffect(() => {
    if (!artifactPreview.open || !previewAssetId) {
      return;
    }
    setArtifactPreviewLoading(projectId, resultAssetQuery.isLoading);
  }, [artifactPreview.open, previewAssetId, projectId, resultAssetQuery.isLoading, setArtifactPreviewLoading]);

  useEffect(() => {
    if (!artifactPreview.open) {
      return;
    }
    if (resultAssetQuery.error instanceof Error) {
      setArtifactPreviewError(projectId, resultAssetQuery.error.message);
      return;
    }
    if (resultAssetQuery.data) {
      setArtifactPreviewError(projectId, undefined);
    }
  }, [artifactPreview.open, projectId, resultAssetQuery.data, resultAssetQuery.error, setArtifactPreviewError]);

  function reportActionError(error: unknown, fallback: string) {
    setNotice(projectId, error instanceof Error ? error.message : fallback);
  }

  function persistRuntimePreference(payload: Parameters<typeof api.updateProjectRuntimePreferences>[1], fallback: string) {
    void updateProjectRuntimePreferencesMutation
      .mutateAsync(payload)
      .catch((error) => reportActionError(error, fallback));
  }

  function handleOpenAssetPreview(assetId: string, source: "card" | "results" | "files", cardId?: string) {
    openArtifactPreview(projectId, {
      projectId,
      assetId,
      cardId,
      source,
    });
  }

  function handleSendAssetToManager() {
    const detail = resultAssetQuery.data;
    if (!detail) return;
    addAttachment(projectId, {
      type: "asset",
      id: detail.asset.asset_id,
      label: detail.asset.title,
    });
    setNotice(projectId, `已将 ${detail.asset.title} 加入 Manager 上下文。`);
    setMobileTab(projectId, "chat");
  }

  function handleExplainAsset() {
    const detail = resultAssetQuery.data;
    if (!detail) return;
    addAttachment(projectId, {
      type: "asset",
      id: detail.asset.asset_id,
      label: detail.asset.title,
    });
    setDraftMessage(projectId, `请解释结果 ${detail.asset.title}，重点说明结论、可信度和下一步动作。`);
    setNotice(projectId, `已把 ${detail.asset.title} 送到 Manager。`);
    setMobileTab(projectId, "chat");
  }

  async function handleStartRun(card: Card) {
    setNotice(projectId, null);
    setSelectedCard(projectId, card.card_id);
    const selectedProfileId = selectedProfileByProject[card.card_id];
    const selectedProfile = executorProfiles.find((p) => p.enabled && p.profile_id === selectedProfileId);
    let workerType: string | undefined;
    let profileId: string | undefined;
    if (selectedProfile) {
      workerType = selectedProfile.worker_type;
      profileId = selectedProfile.profile_id;
    } else {
      workerType = selectedWorkerByProject[card.card_id] ?? configuredWorkers[0]?.worker_type;
      profileId = preferredExecutorProfile(executorProfiles.filter((p) => p.enabled), workerType)?.profile_id;
    }
    const pythonRuntime = selectedPythonRuntimeByProject[card.card_id] ?? effectiveGlobalPythonRuntime ?? "__system__";
    const rRuntime = selectedRRuntimeByProject[card.card_id] ?? effectiveGlobalRRuntime ?? "__system__";
    try {
      const response = await startRunMutation.mutateAsync({ cardId: card.card_id, workerType, profileId, pythonRuntime, rRuntime });
      if (response.status === "cancelled") {
        setNotice(projectId, `Run ${response.run_id} 未启动：${response.worker_type} 的权限校验被拒绝。`);
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.detail && typeof error.detail === "object") {
        const blockDetails = (error.detail as { block_details?: { blocked_by_card_ids?: string[]; block_reasons?: string[] } }).block_details;
        const blockers = [...(blockDetails?.blocked_by_card_ids ?? []), ...(blockDetails?.block_reasons ?? [])].filter(Boolean);
        setNotice(projectId, blockers.length ? `当前不能启动：${blockers.join(", ")}` : error.message);
        return;
      }
      setNotice(projectId, error instanceof Error ? error.message : "启动 run 失败。");
    }
  }

  async function handleReviewRun(card: Card) {
    const latestRun = card.linked_runs.at(-1);
    if (!latestRun) return;
    setNotice(projectId, null);
    try {
      await reviewRunMutation.mutateAsync({ runId: latestRun, accept: true });
    } catch (error) {
      reportActionError(error, "审核 run 失败。");
    }
  }

  async function handleMoveReport(itemId: string, direction: "up" | "down") {
    const sections = reportQuery.data?.sections ?? [];
    const index = sections.findIndex((item) => item.item_id === itemId);
    if (index < 0) return;
    const next = [...sections];
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= next.length) return;
    [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
    try {
      await reorderReportMutation.mutateAsync(next.map((item) => item.item_id).filter((value) => !value.startsWith("report_selected_")));
    } catch (error) {
      reportActionError(error, "调整报告章节顺序失败。");
    }
  }

  async function handleExportReport() {
    try {
      const response = await exportReportMutation.mutateAsync();
      setLastReportExport(response);
      setNotice(projectId, `报告已导出到 ${response.path}`);
    } catch (error) {
      reportActionError(error, "导出报告失败。");
    }
  }

  const loading = projectQuery.isLoading || !snapshot;
  const error = projectQuery.error instanceof Error ? projectQuery.error.message : null;

  if (loading) {
    return (
      <div className="page-shell">
        <SideNav projectId={projectId} current={view} />
        <main className="content">
          <div className="panel" style={{ padding: 40, textAlign: "center" }}>
            {error ? `加载失败: ${error}` : "加载中…"}
          </div>
        </main>
      </div>
    );
  }

  const tasksContent = (
    <div className="stack">
      <div className="workspace-two-col task-workspace">
        <ManagerChatPanel
          projectId={projectId}
          sessionId={currentChatSessionId}
          managerAuto={managerAuto}
          mentionableAssets={snapshot.graph.assets}
          onRefresh={refreshWorkspace}
        />
        <CardStream
          projectId={projectId}
          cards={snapshot.cards}
          workOrder={workOrderQuery.data}
          selectedCardId={selectedCard?.card_id}
          cardInteractionOrder={cardInteractionOrder}
          readOnly={autoLocked}
          onSelect={(card) => setSelectedCard(projectId, card.card_id)}
          onClearSelection={() => setSelectedCard(projectId, null)}
          onStartRun={handleStartRun}
          onReviewRun={handleReviewRun}
          onAskManager={(text) => {
            setDraftMessage(projectId, text);
            setMobileTab(projectId, "chat");
          }}
          onPreviewAsset={(assetId, cardId) => handleOpenAssetPreview(assetId, "card", cardId)}
          workerCapabilities={snapshot.worker_capabilities}
          executorProfiles={executorProfiles}
          selectedWorkerByCard={selectedWorkerByProject}
          selectedProfileByCard={selectedProfileByProject}
          onSelectWorker={(card, workerType) => {
            if (autoLocked) return;
            setSelectedWorker(projectId, card.card_id, workerType);
            setNotice(projectId, `Card ${card.card_id} 将使用 ${workerType} 执行。`);
          }}
          onSelectProfile={(card, profileId) => {
            if (autoLocked) return;
            setSelectedProfile(projectId, card.card_id, profileId);
            setNotice(projectId, `Card ${card.card_id} 将使用 profile ${profileId} 执行。`);
          }}
          pythonRuntimes={snapshot.python_runtimes ?? []}
          rRuntimes={snapshot.r_runtimes ?? []}
          globalPythonRuntime={effectiveGlobalPythonRuntime}
          globalRRuntime={effectiveGlobalRRuntime}
          selectedPythonRuntimeByCard={selectedPythonRuntimeByProject}
          selectedRRuntimeByCard={selectedRRuntimeByProject}
          onSelectPythonRuntime={(card, runtime) => {
            if (autoLocked) return;
            setSelectedPythonRuntime(projectId, card.card_id, runtime);
            setNotice(projectId, `Card ${card.card_id} Python runtime: ${runtime ? formatRuntime(runtime) : "follow global"}。`);
          }}
          onSelectRRuntime={(card, runtime) => {
            if (autoLocked) return;
            setSelectedRRuntime(projectId, card.card_id, runtime);
            setNotice(projectId, `Card ${card.card_id} R runtime: ${runtime ? formatRuntime(runtime) : "follow global"}。`);
          }}
        />
      </div>
    </div>
  );

  return (
    <div className="page-shell">
      <SideNav
        projectId={projectId}
        current={view}
        pythonRuntimes={snapshot.python_runtimes ?? []}
        rRuntimes={snapshot.r_runtimes ?? []}
        globalPythonRuntime={effectiveGlobalPythonRuntime}
        globalRRuntime={effectiveGlobalRRuntime}
        scriptPreference={effectiveScriptPreference}
        managerAuto={managerAuto}
        currentChatSessionId={currentChatSessionId}
        onSelectGlobalPythonRuntime={(runtime) => {
          if (autoLocked) return;
          const normalizedRuntime = runtime === "__system__" ? undefined : runtime;
          setGlobalPythonRuntime(projectId, normalizedRuntime);
          persistRuntimePreference({ python_runtime: normalizedRuntime ?? null }, "保存 Python runtime 失败。");
          setNotice(projectId, `全局 Python runtime: ${formatRuntime(runtime)}。`);
        }}
        onSelectGlobalRRuntime={(runtime) => {
          if (autoLocked) return;
          const normalizedRuntime = runtime === "__system__" ? undefined : runtime;
          setGlobalRRuntime(projectId, normalizedRuntime);
          persistRuntimePreference({ r_runtime: normalizedRuntime ?? null }, "保存 R runtime 失败。");
          setNotice(projectId, `全局 R runtime: ${formatRuntime(runtime)}。`);
        }}
        onSelectScriptPreference={(preference) => {
          if (autoLocked) return;
          setScriptPreference(projectId, preference);
          persistRuntimePreference({ script_preference: preference }, "保存脚本偏好失败。");
          const label =
            preference === "prefer_python"
              ? "偏好 Python"
              : preference === "prefer_r"
                ? "偏好 R"
                : preference === "prefer_mixed"
                  ? "按任务选择"
                  : "让 Manager 询问";
          setNotice(projectId, `脚本偏好: ${label}。`);
        }}
      />
      <main className={`content ${view === "tasks" ? "task-content" : ""}`}>
        {view !== "tasks" ? (
          <ProjectHeader
            summary={snapshot.summary}
            title={
              view === "results"
                ? "Accepted and candidate results"
                : view === "files"
              ? "Uploads, data assets, and execution files"
              : view === "report"
              ? "Report assembly"
              : view === "settings"
              ? "Runtime, libraries, and API settings"
                : "Graph and Git history"
            }
          />
        ) : null}
        {notice ? <div className="notice-panel notice-toast">{notice}</div> : null}
        {/* Desktop */}
        {!isMobileWorkspace ? (
        <div className="desktop-content">
          {view === "tasks" ? tasksContent : null}
          {view === "results" ? (
            <div className="stack">
              <ResultsOverviewChart
                accepted={resultsQuery.data?.accepted ?? []}
                candidate={resultsQuery.data?.candidate ?? []}
                other={resultsQuery.data?.other ?? []}
              />
              <ResultsGrid
                title="Accepted Results"
                items={resultsQuery.data?.accepted ?? []}
                selectedAssetId={selectedAsset?.asset_id}
                onSelect={(asset) => setSelectedAsset(projectId, asset.asset_id)}
                onPreview={(asset) => handleOpenAssetPreview(asset.asset_id, "results")}
              />
              <ResultsGrid
                title="Candidate Results"
                items={resultsQuery.data?.candidate ?? []}
                selectedAssetId={selectedAsset?.asset_id}
                onSelect={(asset) => setSelectedAsset(projectId, asset.asset_id)}
                onPreview={(asset) => handleOpenAssetPreview(asset.asset_id, "results")}
              />
              <ResultsGrid
                title="Other Results"
                items={resultsQuery.data?.other ?? []}
                selectedAssetId={selectedAsset?.asset_id}
                onSelect={(asset) => setSelectedAsset(projectId, asset.asset_id)}
                onPreview={(asset) => handleOpenAssetPreview(asset.asset_id, "results")}
              />
            </div>
          ) : null}
          {view === "files" ? (
            <FilesPanel
              projectId={projectId}
              files={filesQuery.data}
              onRefresh={refreshWorkspace}
              readOnly={autoLocked}
              onPreviewAsset={(asset) => handleOpenAssetPreview(asset.asset_id, "files")}
              onAttachAsset={(asset) => {
                addAttachment(projectId, { type: "asset", id: asset.asset_id, label: asset.title });
                setDraftMessage(projectId, `@${asset.title} `);
                setNotice(projectId, `已将 ${asset.title} 加入 Manager 上下文。`);
              }}
            />
          ) : null}
          {view === "report" ? (
            <div className="stack">
              <ReportBuilder
                sections={reportQuery.data?.sections ?? []}
                onMove={handleMoveReport}
                onExport={handleExportReport}
                exportInfo={lastReportExport}
                selectedSectionId={selectedSection?.item_id}
                onSelect={(itemId) => setSelectedSection(projectId, itemId)}
              />
              <ReportSectionDetailPanel section={selectedSection} />
            </div>
          ) : null}
          {view === "advanced" ? (
            <div className="stack">
              <AdvancedPanels
                graph={advancedGraphQuery.data?.graph ?? null}
                gitItems={advancedGitQuery.data?.items ?? []}
                readOnly={autoLocked}
                pythonRuntimes={snapshot.python_runtimes ?? []}
                rRuntimes={snapshot.r_runtimes ?? []}
                globalPythonRuntime={effectiveGlobalPythonRuntime}
                globalRRuntime={effectiveGlobalRRuntime}
                onSelectGlobalPythonRuntime={(runtime) => {
                  if (autoLocked) return;
                  setGlobalPythonRuntime(projectId, runtime);
                  setNotice(projectId, `全局 Python runtime: ${formatRuntime(runtime)}。`);
                }}
                onSelectGlobalRRuntime={(runtime) => {
                  if (autoLocked) return;
                  setGlobalRRuntime(projectId, runtime);
                  setNotice(projectId, `全局 R runtime: ${formatRuntime(runtime)}。`);
                }}
              />
              {selectedCard ? (
                <CardDetailPanel
                  card={selectedCard}
                  summary={snapshot.summary}
                  workItem={selectedWorkItem}
                />
              ) : null}
            </div>
          ) : null}
          {view === "settings" ? (
            <SettingsPanels
              projectId={projectId}
              project={snapshot.project}
              pythonRuntimes={snapshot.python_runtimes ?? []}
              rRuntimes={snapshot.r_runtimes ?? []}
              readOnly={autoLocked}
            />
          ) : null}
        </div>
        ) : null}

        {/* Mobile */}
        {isMobileWorkspace ? (
        <div className="mobile-content">
          {view === "tasks" ? (
            mobileTab === "chat" ? (
              <ManagerChatPanel
                projectId={projectId}
                sessionId={currentChatSessionId}
                managerAuto={managerAuto}
                mentionableAssets={snapshot.graph.assets}
                onRefresh={refreshWorkspace}
              />
            ) : (
              <CardStream
                projectId={projectId}
                cards={snapshot.cards}
                workOrder={workOrderQuery.data}
                selectedCardId={selectedCard?.card_id}
                cardInteractionOrder={cardInteractionOrder}
                readOnly={autoLocked}
                onSelect={(card) => setSelectedCard(projectId, card.card_id)}
                onClearSelection={() => setSelectedCard(projectId, null)}
                onStartRun={handleStartRun}
                onReviewRun={handleReviewRun}
                onAskManager={(text) => {
                  setDraftMessage(projectId, text);
                  setMobileTab(projectId, "chat");
                }}
                onPreviewAsset={(assetId, cardId) => handleOpenAssetPreview(assetId, "card", cardId)}
                workerCapabilities={snapshot.worker_capabilities}
                executorProfiles={executorProfiles}
                selectedWorkerByCard={selectedWorkerByProject}
                selectedProfileByCard={selectedProfileByProject}
                onSelectWorker={(card, workerType) => {
                  if (autoLocked) return;
                  setSelectedWorker(projectId, card.card_id, workerType);
                  setNotice(projectId, `Card ${card.card_id} 将使用 ${workerType} 执行。`);
                }}
                onSelectProfile={(card, profileId) => {
                  if (autoLocked) return;
                  setSelectedProfile(projectId, card.card_id, profileId);
                  setNotice(projectId, `Card ${card.card_id} 将使用 profile ${profileId} 执行。`);
                }}
                pythonRuntimes={snapshot.python_runtimes ?? []}
                rRuntimes={snapshot.r_runtimes ?? []}
                globalPythonRuntime={effectiveGlobalPythonRuntime}
                globalRRuntime={effectiveGlobalRRuntime}
                selectedPythonRuntimeByCard={selectedPythonRuntimeByProject}
                selectedRRuntimeByCard={selectedRRuntimeByProject}
                onSelectPythonRuntime={(card, runtime) => {
                  if (autoLocked) return;
                  setSelectedPythonRuntime(projectId, card.card_id, runtime);
                  setNotice(projectId, `Card ${card.card_id} Python runtime: ${runtime ? formatRuntime(runtime) : "follow global"}。`);
                }}
                onSelectRRuntime={(card, runtime) => {
                  if (autoLocked) return;
                  setSelectedRRuntime(projectId, card.card_id, runtime);
                  setNotice(projectId, `Card ${card.card_id} R runtime: ${runtime ? formatRuntime(runtime) : "follow global"}。`);
                }}
              />
            )
          ) : null}
          {view !== "tasks" ? (
            <div className="stack">
              {view === "results" ? (
                <>
                  <ResultsOverviewChart
                    accepted={resultsQuery.data?.accepted ?? []}
                    candidate={resultsQuery.data?.candidate ?? []}
                    other={resultsQuery.data?.other ?? []}
                  />
                  <ResultsGrid
                    title="Results"
                    items={allResultAssets}
                    selectedAssetId={selectedAsset?.asset_id}
                    onSelect={(asset) => setSelectedAsset(projectId, asset.asset_id)}
                    onPreview={(asset) => handleOpenAssetPreview(asset.asset_id, "results")}
                  />
                </>
              ) : null}
              {view === "report" ? (
                <>
                  <ReportBuilder
                    sections={reportQuery.data?.sections ?? []}
                    onMove={handleMoveReport}
                    onExport={handleExportReport}
                    exportInfo={lastReportExport}
                    selectedSectionId={selectedSection?.item_id}
                    onSelect={(itemId) => setSelectedSection(projectId, itemId)}
                  />
                  <ReportSectionDetailPanel section={selectedSection} />
                </>
              ) : null}
              {view === "files" ? (
                <FilesPanel
                  projectId={projectId}
                  files={filesQuery.data}
                  onRefresh={refreshWorkspace}
                  readOnly={autoLocked}
                  onPreviewAsset={(asset) => handleOpenAssetPreview(asset.asset_id, "files")}
                  onAttachAsset={(asset) => {
                    addAttachment(projectId, { type: "asset", id: asset.asset_id, label: asset.title });
                    setDraftMessage(projectId, `@${asset.title} `);
                    setNotice(projectId, `已将 ${asset.title} 加入 Manager 上下文。`);
                  }}
                />
              ) : null}
              {view === "advanced" ? (
                <>
                  <AdvancedPanels
                    graph={advancedGraphQuery.data?.graph ?? null}
                    gitItems={advancedGitQuery.data?.items ?? []}
                    readOnly={autoLocked}
                    pythonRuntimes={snapshot.python_runtimes ?? []}
                    rRuntimes={snapshot.r_runtimes ?? []}
                    globalPythonRuntime={effectiveGlobalPythonRuntime}
                    globalRRuntime={effectiveGlobalRRuntime}
                    onSelectGlobalPythonRuntime={(runtime) => {
                      setGlobalPythonRuntime(projectId, runtime);
                      setNotice(projectId, `全局 Python runtime: ${formatRuntime(runtime)}。`);
                    }}
                    onSelectGlobalRRuntime={(runtime) => {
                      setGlobalRRuntime(projectId, runtime);
                      setNotice(projectId, `全局 R runtime: ${formatRuntime(runtime)}。`);
                    }}
                  />
                  {selectedCard ? (
                    <CardDetailPanel
                      card={selectedCard}
                      summary={snapshot.summary}
                      workItem={selectedWorkItem}
                    />
                  ) : null}
                </>
              ) : null}
              {view === "settings" ? (
                <SettingsPanels
                  projectId={projectId}
                  project={snapshot.project}
                  pythonRuntimes={snapshot.python_runtimes ?? []}
                  rRuntimes={snapshot.r_runtimes ?? []}
                  readOnly={autoLocked}
                />
              ) : null}
            </div>
          ) : null}
        </div>
        ) : null}

        {artifactPreview.open ? (
          <div
            className="artifact-preview-drawer"
            onClick={() => closeArtifactPreview(projectId)}
            role="presentation"
          >
            <ResultPreviewPanel
              detail={resultAssetQuery.data}
              mode="drawer"
              title="Artifact Preview"
              loading={artifactPreview.loading}
              error={artifactPreview.error}
              onClose={() => closeArtifactPreview(projectId)}
            />
          </div>
        ) : null}

        {/* Mobile Tabs */}
        {view === "tasks" ? (
          <div className="mobile-tabs">
            <button
              className={`mobile-tab ${mobileTab === "chat" ? "active" : ""}`}
              onClick={() => setMobileTab(projectId, "chat")}
            >
              💬 聊天
            </button>
            <button
              className={`mobile-tab ${mobileTab === "blueprint" ? "active" : ""}`}
              onClick={() => setMobileTab(projectId, "blueprint")}
            >
              🧬 蓝图
            </button>
          </div>
        ) : null}
      </main>
    </div>
  );
}
