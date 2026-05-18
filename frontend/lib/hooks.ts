"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";

export function useProjectSnapshot(projectId: string) {
  return useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => api.getProject(projectId),
  });
}

export function useProjectResults(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.results(projectId),
    queryFn: () => api.getResults(projectId),
    enabled,
  });
}

export function useResultAsset(projectId: string, assetId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.resultAsset(projectId, assetId ?? "none"),
    queryFn: () => api.getResultAsset(projectId, assetId!),
    enabled: enabled && Boolean(assetId),
  });
}

export function useProjectReport(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.report(projectId),
    queryFn: () => api.getReport(projectId),
    enabled,
  });
}

export function useAdvancedGraph(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.advancedGraph(projectId),
    queryFn: () => api.getAdvancedGraph(projectId),
    enabled,
  });
}

export function useAdvancedGit(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.advancedGit(projectId),
    queryFn: () => api.getAdvancedGit(projectId),
    enabled,
  });
}

export function useAdvancedProposals(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.advancedProposals(projectId),
    queryFn: () => api.getAdvancedProposals(projectId),
    enabled,
  });
}

export function useRunEvents(projectId: string, runId?: string, runStatus?: string) {
  return useQuery({
    queryKey: queryKeys.runEvents(projectId, runId ?? "none"),
    queryFn: () => api.getRunEvents(projectId, runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      if (runStatus && ["success", "failed", "cancelled"].includes(runStatus)) {
        return false;
      }
      const data = query.state.data;
      return data?.items?.length ? 4_000 : 2_000;
    },
  });
}

export function useRuntimeApprovals(projectId: string, runId?: string, runStatus?: string) {
  return useQuery({
    queryKey: queryKeys.runtimeApprovals(projectId, runId ?? "none"),
    queryFn: () => api.getRuntimeApprovals(projectId, runId!),
    enabled: Boolean(runId),
    refetchInterval: runStatus && ["success", "failed", "cancelled"].includes(runStatus) ? false : 4_000,
  });
}

export function useWorkspaceRefresh(projectId: string) {
  const queryClient = useQueryClient();
  return async function refreshWorkspace() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.results(projectId) }),
      queryClient.invalidateQueries({ queryKey: ["result-asset", projectId] }),
      queryClient.invalidateQueries({ queryKey: queryKeys.report(projectId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.advancedGraph(projectId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.advancedGit(projectId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.advancedProposals(projectId) }),
    ]);
  };
}

export function useStartRunMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ cardId, workerType }: { cardId: string; workerType?: string }) => api.startRun(projectId, cardId, workerType),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useReviewRunMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ runId, accept }: { runId: string; accept: boolean }) => api.reviewRun(projectId, runId, accept),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useRuntimeApprovalDecisionMutation(projectId: string, runId?: string) {
  const queryClient = useQueryClient();
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ requestId, approve }: { requestId: string; approve: boolean }) =>
      api.decideRuntimeApproval(projectId, runId!, requestId, approve),
    onSuccess: async () => {
      if (runId) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.runtimeApprovals(projectId, runId) }),
          queryClient.invalidateQueries({ queryKey: queryKeys.runEvents(projectId, runId) }),
        ]);
      }
      await refresh();
    },
  });
}

export function useModifyProposalMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ proposalId, message }: { proposalId: string; message: string }) => api.modifyProposal(projectId, proposalId, message),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useReportReorderMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (itemIds: string[]) => api.reorderReport(projectId, itemIds),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.report(projectId), data);
    },
  });
}

export function useReportExportMutation(projectId: string) {
  return useMutation({
    mutationFn: () => api.exportReportHtml(projectId),
  });
}
