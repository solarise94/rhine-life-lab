"use client";

import { useEffect, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { ApiError, api } from "@/lib/api";
import {
  useAdvancedGit,
  useAdvancedGraph,
  useCancelRunMutation,
  useCleanupRunMutation,
  useProjectFiles,
  useProjectReport,
  useProjectResults,
  useProjectSnapshot,
  useRerunCardMutation,
  useReportExportMutation,
  useReportReorderMutation,
  useResetCardRunStateMutation,
  useResultAsset,
  useReviewRunMutation,
  useRunEvents,
  useRuntimeApprovalDecisionMutation,
  useRuntimeApprovals,
  useStartRunMutation,
  useWorkOrder,
  useWorkspaceRefresh,
} from "@/lib/hooks";
import { queryKeys } from "@/lib/query-keys";
import { useReportViewStore } from "@/lib/stores/report-view-store";
import { useResultsViewStore } from "@/lib/stores/results-view-store";
import { EMPTY_SELECTED_RUNTIME_BY_CARD, EMPTY_SELECTED_WORKER_BY_CARD, useWorkspaceUiStore } from "@/lib/stores/workspace-ui-store";
import { Card, RunEvent } from "@/lib/types";
import { SideNav } from "./SideNav";
import { ProjectHeader } from "./ProjectHeader";
import { ManagerChatPanel } from "@/components/manager-chat/ManagerChatPanel";
import { CardStream } from "@/components/cards/CardStream";
import { CardDetailPanel } from "@/components/detail/CardDetailPanel";
import { RunEventsPanel } from "@/components/detail/RunEventsPanel";
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

type View = "tasks" | "results" | "files" | "report" | "advanced";

function formatPythonRuntime(runtime?: string) {
  if (!runtime || runtime === "__system__") return "system";
  return runtime;
}

export function ProjectWorkspace({ projectId, view }: { projectId: string; view: View }) {
  const queryClient = useQueryClient();
  const selectedCardId = useWorkspaceUiStore((s) => s.selectedCardByProject[projectId]);
  const selectedWorkerByProject = useWorkspaceUiStore((s) => s.selectedWorkerByProject[projectId] ?? EMPTY_SELECTED_WORKER_BY_CARD);
  const globalPythonRuntime = useWorkspaceUiStore((s) => s.globalPythonRuntimeByProject[projectId]);
  const selectedPythonRuntimeByProject = useWorkspaceUiStore((s) => s.selectedPythonRuntimeByProject[projectId] ?? EMPTY_SELECTED_RUNTIME_BY_CARD);
  const currentChatSessionId = useWorkspaceUiStore((s) => s.currentChatSessionIdByProject[projectId] ?? null);
  const notice = useWorkspaceUiStore((s) => s.noticesByProject[projectId] ?? null);
  const setSelectedCard = useWorkspaceUiStore((s) => s.setSelectedCard);
  const setSelectedWorker = useWorkspaceUiStore((s) => s.setSelectedWorker);
  const setGlobalPythonRuntime = useWorkspaceUiStore((s) => s.setGlobalPythonRuntime);
  const setSelectedPythonRuntime = useWorkspaceUiStore((s) => s.setSelectedPythonRuntime);
  const setNotice = useWorkspaceUiStore((s) => s.setNotice);
  const mobileTab = useWorkspaceUiStore((s) => s.mobileTabByProject[projectId] ?? "chat");
  const setMobileTab = useWorkspaceUiStore((s) => s.setMobileTab);

  const selectedAssetId = useResultsViewStore((s) => s.selectedAssetByProject[projectId]);
  const setSelectedAsset = useResultsViewStore((s) => s.setSelectedAsset);
  const selectedSectionId = useReportViewStore((s) => s.selectedSectionByProject[projectId]);
  const setSelectedSection = useReportViewStore((s) => s.setSelectedSection);
  const refreshWorkspace = useWorkspaceRefresh(projectId);

  const projectQuery = useProjectSnapshot(projectId);
  const workOrderQuery = useWorkOrder(projectId, view === "tasks");
  const resultsQuery = useProjectResults(projectId, view === "results");
  const filesQuery = useProjectFiles(projectId, view === "files");
  const reportQuery = useProjectReport(projectId, view === "report");
  const advancedGraphQuery = useAdvancedGraph(projectId, view === "advanced");
  const advancedGitQuery = useAdvancedGit(projectId, view === "advanced");
  const startRunMutation = useStartRunMutation(projectId);
  const cancelRunMutation = useCancelRunMutation(projectId);
  const cleanupRunMutation = useCleanupRunMutation(projectId);
  const resetCardRunStateMutation = useResetCardRunStateMutation(projectId);
  const rerunCardMutation = useRerunCardMutation(projectId);
  const reviewRunMutation = useReviewRunMutation(projectId);
  const reorderReportMutation = useReportReorderMutation(projectId);
  const exportReportMutation = useReportExportMutation(projectId);

  const snapshot = projectQuery.data;
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
  const selectedWorkerType = selectedCard
    ? selectedWorkerByProject[selectedCard.card_id] ?? selectedRun?.worker_type ?? configuredWorkers[0]?.worker_type
    : configuredWorkers[0]?.worker_type;
  const selectedPythonRuntime = selectedCard ? selectedPythonRuntimeByProject[selectedCard.card_id] : undefined;
  const allResultAssets = useMemo(
    () => (resultsQuery.data ? [...resultsQuery.data.accepted, ...resultsQuery.data.candidate, ...resultsQuery.data.other] : []),
    [resultsQuery.data],
  );
  const selectedAsset = allResultAssets.find((item) => item.asset_id === selectedAssetId) ?? allResultAssets[0];
  const selectedSection = reportQuery.data?.sections.find((item) => item.item_id === selectedSectionId) ?? reportQuery.data?.sections[0];

  const resultAssetQuery = useResultAsset(projectId, view === "results" ? selectedAsset?.asset_id : undefined, view === "results");
  const runEventsQuery = useRunEvents(projectId, view === "tasks" ? selectedRunId : undefined, selectedRun?.status);
  const runtimeApprovalsQuery = useRuntimeApprovals(projectId, view === "tasks" ? selectedRunId : undefined, selectedRun?.status);
  const runtimeApprovalMutation = useRuntimeApprovalDecisionMutation(projectId, view === "tasks" ? selectedRunId : undefined);

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
    if (view !== "results" || !allResultAssets.length || selectedAssetId) return;
    setSelectedAsset(projectId, allResultAssets[0].asset_id);
  }, [allResultAssets, projectId, selectedAssetId, setSelectedAsset, view]);

  useEffect(() => {
    if (view !== "report" || !reportQuery.data?.sections.length || selectedSectionId) return;
    setSelectedSection(projectId, reportQuery.data.sections[0].item_id);
  }, [projectId, reportQuery.data, selectedSectionId, setSelectedSection, view]);

  useEffect(() => {
    if (view !== "tasks" || activeRunCount === 0) return;
    const timer = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.workOrder(projectId) });
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [activeRunCount, projectId, queryClient, view]);

  useEffect(() => {
    if (view !== "tasks" || !selectedRunId) return;
    const wsUrl = api.getRunEventsWsUrl(projectId, selectedRunId);
    if (!wsUrl) return;
    const socket = new WebSocket(wsUrl);
    socket.onerror = () => socket.close();
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as RunEvent;
      queryClient.setQueryData(queryKeys.runEvents(projectId, selectedRunId), (previous: { items: RunEvent[] } | undefined) => {
        const items = previous?.items ?? [];
        if (items.some((item) => item.event_id === payload.event_id)) return previous;
        return { items: [...items, payload] };
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.workOrder(projectId) });
    };
    return () => socket.close();
  }, [projectId, queryClient, selectedRunId, view]);

  function reportActionError(error: unknown, fallback: string) {
    setNotice(projectId, error instanceof Error ? error.message : fallback);
  }

  async function handleStartRun(card: Card) {
    setNotice(projectId, null);
    setSelectedCard(projectId, card.card_id);
    const workerType = selectedWorkerByProject[card.card_id] ?? configuredWorkers[0]?.worker_type;
    const pythonRuntime = selectedPythonRuntimeByProject[card.card_id] ?? globalPythonRuntime ?? "__system__";
    try {
      const response = await startRunMutation.mutateAsync({ cardId: card.card_id, workerType, pythonRuntime });
      if (response.latest_event) {
        queryClient.setQueryData(queryKeys.runEvents(projectId, response.run_id), { items: [response.latest_event] });
      }
      if (response.status === "needs_approval") {
        setNotice(projectId, `Run ${response.run_id} 已用 ${response.worker_type} 启动，正在等待运行时批准。`);
      } else if (response.status === "cancelled") {
        setNotice(projectId, `Run ${response.run_id} 未启动：${response.worker_type} 的权限校验被拒绝。`);
      } else if (response.latest_event?.message) {
        setNotice(projectId, `[${response.worker_type}] ${response.latest_event.message}`);
      } else {
        setNotice(projectId, `Run ${response.run_id} 已用 ${response.worker_type} 启动。`);
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

  async function handleCancelRun() {
    if (!selectedRunId) return;
    setNotice(projectId, null);
    try {
      const response = await cancelRunMutation.mutateAsync({ runId: selectedRunId });
      setNotice(projectId, `Run ${response.run_id} 已取消。`);
    } catch (error) {
      reportActionError(error, "取消 run 失败。");
    }
  }

  async function handleCleanupRun() {
    if (!selectedRunId) return;
    setNotice(projectId, null);
    try {
      const response = await cleanupRunMutation.mutateAsync({ runId: selectedRunId });
      setNotice(projectId, `Run ${response.run_id} 已清理归档。`);
    } catch (error) {
      reportActionError(error, "清理 run 失败。");
    }
  }

  async function handleResetCardRunState() {
    if (!selectedCard) return;
    setNotice(projectId, null);
    try {
      const response = await resetCardRunStateMutation.mutateAsync({ cardId: selectedCard.card_id });
      setNotice(projectId, `Card ${response.card_id} 已重置为 ${response.status}。`);
    } catch (error) {
      reportActionError(error, "重置 card 状态失败。");
    }
  }

  async function handleRerunCard() {
    if (!selectedCard) return;
    setNotice(projectId, null);
    const workerType = selectedWorkerByProject[selectedCard.card_id] ?? configuredWorkers[0]?.worker_type;
    const pythonRuntime = selectedPythonRuntimeByProject[selectedCard.card_id] ?? globalPythonRuntime ?? "__system__";
    try {
      const response = await rerunCardMutation.mutateAsync({ cardId: selectedCard.card_id, workerType, pythonRuntime });
      if (response.latest_event) {
        queryClient.setQueryData(queryKeys.runEvents(projectId, response.run_id), { items: [response.latest_event] });
      }
      setSelectedCard(projectId, selectedCard.card_id);
      setNotice(projectId, response.latest_event?.message ?? `已用 ${response.worker_type} 重新创建 run ${response.run_id}。`);
    } catch (error) {
      reportActionError(error, "重新运行 card 失败。");
    }
  }

  async function handleApprovalDecision(requestId: string, approve: boolean) {
    if (!selectedRunId) return;
    try {
      await runtimeApprovalMutation.mutateAsync({ requestId, approve });
      setNotice(projectId, approve ? `已批准 ${requestId}` : `已拒绝 ${requestId}`);
    } catch (error) {
      reportActionError(error, approve ? "批准权限请求失败。" : "拒绝权限请求失败。");
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
    await reorderReportMutation.mutateAsync(next.map((item) => item.item_id).filter((value) => !value.startsWith("report_selected_")));
  }

  async function handleExportReport() {
    const response = await exportReportMutation.mutateAsync();
    setNotice(projectId, `报告已导出到 ${response.path}`);
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
          mentionableAssets={snapshot.graph.assets}
          onRefresh={refreshWorkspace}
        />
        <CardStream
          projectId={projectId}
          cards={snapshot.cards}
          workOrder={workOrderQuery.data}
          selectedCardId={selectedCard?.card_id}
          onSelect={(card) => setSelectedCard(projectId, card.card_id)}
          onClearSelection={() => setSelectedCard(projectId, null)}
          onStartRun={handleStartRun}
          onReviewRun={handleReviewRun}
          onAskManager={(text) => {
            const store = useWorkspaceUiStore.getState();
            store.setDraftMessage(projectId, text);
            store.setMobileTab(projectId, "chat");
          }}
          workerCapabilities={snapshot.worker_capabilities}
          selectedWorkerByCard={selectedWorkerByProject}
          onSelectWorker={(card, workerType) => {
            setSelectedWorker(projectId, card.card_id, workerType);
            setNotice(projectId, `Card ${card.card_id} 将使用 ${workerType} 执行。`);
          }}
        />
      </div>
      <CardDetailPanel
        card={selectedCard}
        summary={snapshot.summary}
        workItem={selectedWorkItem}
        run={selectedRun}
        latestEvent={runEventsQuery.data?.items?.at(-1)}
        workerCapabilities={snapshot.worker_capabilities}
        selectedWorkerType={selectedWorkerType}
        pythonRuntimes={snapshot.python_runtimes ?? []}
        globalPythonRuntime={globalPythonRuntime}
        selectedPythonRuntime={selectedPythonRuntime}
        onSelectGlobalPythonRuntime={(runtime) => {
          setGlobalPythonRuntime(projectId, runtime);
          setNotice(projectId, `全局 Python runtime: ${formatPythonRuntime(runtime)}。`);
        }}
        onSelectPythonRuntime={(runtime) => {
          if (!selectedCard) return;
          setSelectedPythonRuntime(projectId, selectedCard.card_id, runtime);
          setNotice(projectId, `Card ${selectedCard.card_id} Python runtime: ${runtime ? formatPythonRuntime(runtime) : "follow global"}。`);
        }}
        onSelectWorker={(workerType) => {
          if (!selectedCard) return;
          setSelectedWorker(projectId, selectedCard.card_id, workerType);
          setNotice(projectId, `Card ${selectedCard.card_id} 将使用 ${workerType} 执行。`);
        }}
      />
    </div>
  );

  return (
    <div className="page-shell">
      <SideNav projectId={projectId} current={view} />
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
                : "Graph and Git history"
            }
          />
        ) : null}
        {notice ? (
          <div className="notice-panel">{notice}</div>
        ) : null}
        {error ? (
          <div className="notice-panel error">{error}</div>
        ) : null}

        {/* Desktop */}
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
              />
              <ResultsGrid
                title="Candidate Results"
                items={resultsQuery.data?.candidate ?? []}
                selectedAssetId={selectedAsset?.asset_id}
                onSelect={(asset) => setSelectedAsset(projectId, asset.asset_id)}
              />
              <ResultsGrid
                title="Other Results"
                items={resultsQuery.data?.other ?? []}
                selectedAssetId={selectedAsset?.asset_id}
                onSelect={(asset) => setSelectedAsset(projectId, asset.asset_id)}
              />
              <ResultPreviewPanel detail={resultAssetQuery.data} />
            </div>
          ) : null}
          {view === "files" ? (
            <FilesPanel
              projectId={projectId}
              files={filesQuery.data}
              onRefresh={refreshWorkspace}
              onAttachAsset={(asset) => {
                const store = useWorkspaceUiStore.getState();
                store.addAttachment(projectId, { type: "asset", id: asset.asset_id, label: asset.title });
                store.setDraftMessage(projectId, `@${asset.title} `);
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
              />
              <CardDetailPanel
                card={selectedCard}
                summary={snapshot.summary}
                workerCapabilities={snapshot.worker_capabilities}
                selectedWorkerType={selectedWorkerType}
                pythonRuntimes={snapshot.python_runtimes ?? []}
                globalPythonRuntime={globalPythonRuntime}
                selectedPythonRuntime={selectedPythonRuntime}
                onSelectGlobalPythonRuntime={(runtime) => {
                  setGlobalPythonRuntime(projectId, runtime);
                  setNotice(projectId, `全局 Python runtime: ${formatPythonRuntime(runtime)}。`);
                }}
                onSelectPythonRuntime={(runtime) => {
                  if (!selectedCard) return;
                  setSelectedPythonRuntime(projectId, selectedCard.card_id, runtime);
                  setNotice(projectId, `Card ${selectedCard.card_id} Python runtime: ${runtime ? formatPythonRuntime(runtime) : "follow global"}。`);
                }}
                onSelectWorker={(workerType) => {
                  if (!selectedCard) return;
                  setSelectedWorker(projectId, selectedCard.card_id, workerType);
                  setNotice(projectId, `Card ${selectedCard.card_id} 将使用 ${workerType} 执行。`);
                }}
              />
            </div>
          ) : null}
        </div>

        {/* Mobile */}
        <div className="mobile-content">
          {view === "tasks" ? (
            mobileTab === "chat" ? (
              <ManagerChatPanel
                projectId={projectId}
                mentionableAssets={snapshot.graph.assets}
                onRefresh={refreshWorkspace}
              />
            ) : (
              <CardStream
                projectId={projectId}
                cards={snapshot.cards}
                workOrder={workOrderQuery.data}
                selectedCardId={selectedCard?.card_id}
                onSelect={(card) => setSelectedCard(projectId, card.card_id)}
                onClearSelection={() => setSelectedCard(projectId, null)}
                onStartRun={handleStartRun}
                onReviewRun={handleReviewRun}
                onAskManager={(text) => {
                  const store = useWorkspaceUiStore.getState();
                  store.setDraftMessage(projectId, text);
                  store.setMobileTab(projectId, "chat");
                }}
                workerCapabilities={snapshot.worker_capabilities}
                selectedWorkerByCard={selectedWorkerByProject}
                onSelectWorker={(card, workerType) => {
                  setSelectedWorker(projectId, card.card_id, workerType);
                  setNotice(projectId, `Card ${card.card_id} 将使用 ${workerType} 执行。`);
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
                  />
                  <ResultPreviewPanel detail={resultAssetQuery.data} />
                </>
              ) : null}
              {view === "report" ? (
                <>
                  <ReportBuilder
                    sections={reportQuery.data?.sections ?? []}
                    onMove={handleMoveReport}
                    onExport={handleExportReport}
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
                  onAttachAsset={(asset) => {
                    const store = useWorkspaceUiStore.getState();
                    store.addAttachment(projectId, { type: "asset", id: asset.asset_id, label: asset.title });
                    store.setDraftMessage(projectId, `@${asset.title} `);
                    setNotice(projectId, `已将 ${asset.title} 加入 Manager 上下文。`);
                  }}
                />
              ) : null}
              {view === "advanced" ? (
                <>
                  <AdvancedPanels
                    graph={advancedGraphQuery.data?.graph ?? null}
                    gitItems={advancedGitQuery.data?.items ?? []}
                  />
                  <CardDetailPanel
                    card={selectedCard}
                    summary={snapshot.summary}
                    workerCapabilities={snapshot.worker_capabilities}
                    selectedWorkerType={selectedWorkerType}
                    pythonRuntimes={snapshot.python_runtimes ?? []}
                    globalPythonRuntime={globalPythonRuntime}
                    selectedPythonRuntime={selectedPythonRuntime}
                    onSelectGlobalPythonRuntime={(runtime) => {
                      setGlobalPythonRuntime(projectId, runtime);
                      setNotice(projectId, `全局 Python runtime: ${formatPythonRuntime(runtime)}。`);
                    }}
                    onSelectPythonRuntime={(runtime) => {
                      if (!selectedCard) return;
                      setSelectedPythonRuntime(projectId, selectedCard.card_id, runtime);
                      setNotice(projectId, `Card ${selectedCard.card_id} Python runtime: ${runtime ? formatPythonRuntime(runtime) : "follow global"}。`);
                    }}
                    onSelectWorker={(workerType) => {
                      if (!selectedCard) return;
                      setSelectedWorker(projectId, selectedCard.card_id, workerType);
                      setNotice(projectId, `Card ${selectedCard.card_id} 将使用 ${workerType} 执行。`);
                    }}
                  />
                </>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* Run Events Panel - shown conditionally */}
        {view === "tasks" && selectedRun ? (
          <div style={{ marginTop: 16 }}>
            <RunEventsPanel
              card={selectedCard}
              run={selectedRun}
              events={runEventsQuery.data?.items ?? []}
              approvals={runtimeApprovalsQuery.data?.items ?? []}
              onApprove={(requestId) => handleApprovalDecision(requestId, true)}
              onReject={(requestId) => handleApprovalDecision(requestId, false)}
              onCancelRun={handleCancelRun}
              onCleanupRun={handleCleanupRun}
              onResetCard={handleResetCardRunState}
              onRerunCard={handleRerunCard}
              actionPending={
                cancelRunMutation.isPending ||
                cleanupRunMutation.isPending ||
                resetCardRunStateMutation.isPending ||
                rerunCardMutation.isPending
              }
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
