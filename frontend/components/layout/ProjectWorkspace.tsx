"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useMemo } from "react";

import { api } from "@/lib/api";
import {
  useAdvancedGit,
  useAdvancedGraph,
  useAdvancedProposals,
  useProjectReport,
  useProjectResults,
  useProjectSnapshot,
  useReportExportMutation,
  useReportReorderMutation,
  useResultAsset,
  useReviewRunMutation,
  useRunEvents,
  useRuntimeApprovalDecisionMutation,
  useRuntimeApprovals,
  useStartRunMutation,
  useWorkspaceRefresh,
} from "@/lib/hooks";
import { queryKeys } from "@/lib/query-keys";
import { useAdvancedViewStore } from "@/lib/stores/advanced-view-store";
import { useReportViewStore } from "@/lib/stores/report-view-store";
import { useResultsViewStore } from "@/lib/stores/results-view-store";
import { useWorkspaceUiStore } from "@/lib/stores/workspace-ui-store";
import { Card, RunEvent } from "@/lib/types";
import { SideNav } from "./SideNav";
import { ProjectHeader } from "./ProjectHeader";
import { ManagerChatPanel } from "@/components/manager-chat/ManagerChatPanel";
import { CardStream } from "@/components/cards/CardStream";
import { CardDetailPanel } from "@/components/detail/CardDetailPanel";
import { RunEventsPanel } from "@/components/detail/RunEventsPanel";
import { ResultsGrid } from "@/components/results/ResultsGrid";
import { ReportBuilder } from "@/components/report/ReportBuilder";

type View = "tasks" | "results" | "report" | "advanced";

const ResultsOverviewChart = dynamic(
  () => import("@/components/results/ResultsOverviewChart").then((module) => module.ResultsOverviewChart),
  { ssr: false },
);
const ResultPreviewPanel = dynamic(
  () => import("@/components/results/ResultPreviewPanel").then((module) => module.ResultPreviewPanel),
  { ssr: false },
);
const ReportSectionDetailPanel = dynamic(
  () => import("@/components/report/ReportSectionDetailPanel").then((module) => module.ReportSectionDetailPanel),
  { ssr: false },
);
const AdvancedPanels = dynamic(
  () => import("@/components/advanced/AdvancedPanels").then((module) => module.AdvancedPanels),
  { ssr: false },
);

export function ProjectWorkspace({ projectId, view }: { projectId: string; view: View }) {
  const queryClient = useQueryClient();
  const selectedCardId = useWorkspaceUiStore((state) => state.selectedCardByProject[projectId]);
  const notice = useWorkspaceUiStore((state) => state.noticesByProject[projectId] ?? null);
  const setSelectedCard = useWorkspaceUiStore((state) => state.setSelectedCard);
  const setNotice = useWorkspaceUiStore((state) => state.setNotice);
  const selectedAssetId = useResultsViewStore((state) => state.selectedAssetByProject[projectId]);
  const setSelectedAsset = useResultsViewStore((state) => state.setSelectedAsset);
  const selectedSectionId = useReportViewStore((state) => state.selectedSectionByProject[projectId]);
  const setSelectedSection = useReportViewStore((state) => state.setSelectedSection);
  const activeAdvancedDocument = useAdvancedViewStore((state) => state.activeDocumentByProject[projectId] ?? "graph");
  const setActiveAdvancedDocument = useAdvancedViewStore((state) => state.setActiveDocument);
  const refreshWorkspace = useWorkspaceRefresh(projectId);

  const projectQuery = useProjectSnapshot(projectId);
  const resultsQuery = useProjectResults(projectId, view === "results");
  const reportQuery = useProjectReport(projectId, view === "report");
  const advancedGraphQuery = useAdvancedGraph(projectId, view === "advanced");
  const advancedGitQuery = useAdvancedGit(projectId, view === "advanced");
  const advancedProposalsQuery = useAdvancedProposals(projectId, view === "advanced");
  const startRunMutation = useStartRunMutation(projectId);
  const reviewRunMutation = useReviewRunMutation(projectId);
  const reorderReportMutation = useReportReorderMutation(projectId);
  const exportReportMutation = useReportExportMutation(projectId);

  const snapshot = projectQuery.data;
  const selectedCard = snapshot?.cards.find((item) => item.card_id === selectedCardId) ?? snapshot?.cards[0];
  const selectedRunId = selectedCard?.linked_runs.at(-1);
  const selectedRun = snapshot?.graph.runs.find((item) => item.run_id === selectedRunId);
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
    if (!snapshot?.cards.length || selectedCardId) {
      return;
    }
    setSelectedCard(projectId, snapshot.cards[0].card_id);
  }, [projectId, selectedCardId, setSelectedCard, snapshot]);

  useEffect(() => {
    if (view !== "results" || !allResultAssets.length || selectedAssetId) {
      return;
    }
    setSelectedAsset(projectId, allResultAssets[0].asset_id);
  }, [allResultAssets, projectId, selectedAssetId, setSelectedAsset, view]);

  useEffect(() => {
    if (view !== "report" || !reportQuery.data?.sections.length || selectedSectionId) {
      return;
    }
    setSelectedSection(projectId, reportQuery.data.sections[0].item_id);
  }, [projectId, reportQuery.data, selectedSectionId, setSelectedSection, view]);

  useEffect(() => {
    if (view !== "tasks" || !selectedRunId) {
      return;
    }
    const wsUrl = api.getRunEventsWsUrl(projectId, selectedRunId);
    if (!wsUrl) {
      return;
    }
    const socket = new WebSocket(wsUrl);
    socket.onerror = () => {
      socket.close();
    };
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as RunEvent;
      queryClient.setQueryData(queryKeys.runEvents(projectId, selectedRunId), (previous: { items: RunEvent[] } | undefined) => {
        const items = previous?.items ?? [];
        if (items.some((item) => item.event_id === payload.event_id)) {
          return previous;
        }
        return { items: [...items, payload] };
      });
    };
    return () => {
      socket.close();
    };
  }, [projectId, queryClient, selectedRunId, view]);

  async function handleStartRun(card: Card) {
    setNotice(projectId, null);
    const response = await startRunMutation.mutateAsync({ cardId: card.card_id });
    if (response.status === "needs_approval") {
      setNotice(projectId, `Run ${response.run_id} is waiting for runtime approval.`);
    }
  }

  async function handleReviewRun(card: Card) {
    const latestRun = card.linked_runs.at(-1);
    if (!latestRun) {
      return;
    }
    setNotice(projectId, null);
    await reviewRunMutation.mutateAsync({ runId: latestRun, accept: true });
  }

  async function handleApprovalDecision(requestId: string, approve: boolean) {
    if (!selectedRunId) {
      return;
    }
    await runtimeApprovalMutation.mutateAsync({ requestId, approve });
    setNotice(projectId, approve ? `Approved ${requestId}` : `Rejected ${requestId}`);
  }

  async function handleMoveReport(itemId: string, direction: "up" | "down") {
    const sections = reportQuery.data?.sections ?? [];
    const index = sections.findIndex((item) => item.item_id === itemId);
    if (index < 0) {
      return;
    }
    const next = [...sections];
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= next.length) {
      return;
    }
    [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
    await reorderReportMutation.mutateAsync(next.map((item) => item.item_id).filter((value) => !value.startsWith("report_selected_")));
  }

  async function handleExportReport() {
    const response = await exportReportMutation.mutateAsync();
    setNotice(projectId, `Report exported to ${response.path}`);
  }

  const loading = projectQuery.isLoading || !snapshot;
  const error = projectQuery.error instanceof Error ? projectQuery.error.message : null;

  if (loading) {
    return <div className="content">{error ? `Load failed: ${error}` : "Loading..."}</div>;
  }

  return (
    <div className="page-shell">
      <SideNav projectId={projectId} current={view} />
      <main className="content">
        <ProjectHeader
          summary={snapshot.summary}
          title={
            view === "tasks"
              ? "Manager chat, task cards, and execution control"
              : view === "results"
                ? "Accepted and candidate results"
                : view === "report"
                  ? "Report assembly from valid assets and claims"
                  : "Graph, proposals, and Git history"
          }
        />
        {notice ? <div className="panel notice-panel">{notice}</div> : null}
        {error ? <div className="panel notice-panel">{error}</div> : null}
        <div className="main-grid">
          <ManagerChatPanel projectId={projectId} proposals={snapshot.proposals} onRefresh={refreshWorkspace} />
          {view === "tasks" ? (
            <CardStream
              cards={snapshot.cards}
              selectedCardId={selectedCard?.card_id}
              onSelect={(card) => setSelectedCard(projectId, card.card_id)}
              onStartRun={handleStartRun}
              onReviewRun={handleReviewRun}
            />
          ) : view === "results" ? (
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
            </div>
          ) : view === "report" ? (
            <ReportBuilder
              sections={reportQuery.data?.sections ?? []}
              onMove={handleMoveReport}
              onExport={handleExportReport}
              selectedSectionId={selectedSection?.item_id}
              onSelect={(itemId) => setSelectedSection(projectId, itemId)}
            />
          ) : (
            <AdvancedPanels
              graph={advancedGraphQuery.data?.graph ?? null}
              gitItems={advancedGitQuery.data?.items ?? []}
              proposals={advancedProposalsQuery.data?.items ?? []}
              activeDocument={activeAdvancedDocument}
              onSelectDocument={(document) => setActiveAdvancedDocument(projectId, document)}
            />
          )}
          <div className="stack">
            {view === "tasks" ? <CardDetailPanel card={selectedCard} summary={snapshot.summary} /> : null}
            {view === "results" ? <ResultPreviewPanel detail={resultAssetQuery.data} /> : null}
            {view === "report" ? <ReportSectionDetailPanel section={selectedSection} /> : null}
            {view === "advanced" ? <CardDetailPanel card={selectedCard} summary={snapshot.summary} /> : null}
            {view === "tasks" ? (
              <RunEventsPanel
                run={selectedRun}
                events={runEventsQuery.data?.items ?? []}
                approvals={runtimeApprovalsQuery.data?.items ?? []}
                onApprove={(requestId) => handleApprovalDecision(requestId, true)}
                onReject={(requestId) => handleApprovalDecision(requestId, false)}
              />
            ) : null}
          </div>
        </div>
      </main>
    </div>
  );
}
