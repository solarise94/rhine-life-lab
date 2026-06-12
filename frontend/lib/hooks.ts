"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import { CreateProjectPayload } from "@/lib/types";

export function useProjects() {
  return useQuery({
    queryKey: queryKeys.projects,
    queryFn: () => api.listProjects(),
  });
}

export function useAppSettings() {
  return useQuery({
    queryKey: queryKeys.appSettings,
    queryFn: () => api.getAppSettings(),
  });
}

export function useLibrary(kind: "skill" | "mcp") {
  return useQuery({
    queryKey: queryKeys.library(kind),
    queryFn: () => api.getLibrary(kind),
  });
}

export function useUpdateAppSettingsMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Parameters<typeof api.updateAppSettings>[0]) => api.updateAppSettings(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.appSettings });
    },
  });
}

export function useTestApiProviderMutation() {
  return useMutation({
    mutationFn: (payload: Parameters<typeof api.testApiProvider>[0]) => api.testApiProvider(payload),
  });
}

export function useExecutorProfiles() {
  return useQuery({
    queryKey: queryKeys.executorProfiles,
    queryFn: () => api.listExecutorProfiles(),
  });
}

export function useSaveExecutorProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profile: Parameters<typeof api.saveExecutorProfile>[0]) => api.saveExecutorProfile(profile),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.executorProfiles });
    },
  });
}

export function useDeleteExecutorProfileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => api.deleteExecutorProfile(profileId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.executorProfiles });
    },
  });
}

export function useCreateProjectMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateProjectPayload) => api.createProject(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects });
    },
  });
}

export function useDeleteProjectMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.deleteProject(projectId),
    onSuccess: async (_data, projectId) => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      queryClient.removeQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}

export function useProjectSnapshot(projectId: string) {
  return useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => api.getProject(projectId),
  });
}

export function useProjectEnvironment(projectId: string) {
  return useQuery({
    queryKey: queryKeys.projectEnvironment(projectId),
    queryFn: () => api.getProjectEnvironment(projectId),
    staleTime: 30_000,
  });
}

export function useManagerAuto(projectId: string, sessionId?: string | null) {
  return useQuery({
    queryKey: queryKeys.managerAuto(projectId, sessionId),
    queryFn: () => api.getManagerAuto(projectId, sessionId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state?.enabled || state?.wake_in_flight ? 4_000 : false;
    },
  });
}

export function useUpdateProjectRuntimePreferencesMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Parameters<typeof api.updateProjectRuntimePreferences>[1]) =>
      api.updateProjectRuntimePreferences(projectId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.projectEnvironment(projectId) });
    },
  });
}

export function useExportDiagnosticsMutation(projectId: string) {
  return useMutation({
    mutationFn: ({ maxRuns }: { maxRuns?: number } = {}) => api.exportDiagnostics(projectId, maxRuns ?? 8),
  });
}

export function useChatSessions(projectId: string, options?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: queryKeys.chatSessions(projectId),
    queryFn: () => api.getChatSessions(projectId),
    refetchInterval: options?.refetchInterval,
  });
}

export function useChatSession(
  projectId: string,
  sessionId: string | undefined,
  enabled: boolean,
  options?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: queryKeys.chatSession(projectId, sessionId ?? "none"),
    queryFn: () => api.getChatSession(projectId, sessionId!),
    enabled: enabled && Boolean(sessionId),
    refetchInterval: options?.refetchInterval,
  });
}

export function useWorkOrder(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.workOrder(projectId),
    queryFn: () => api.getWorkOrder(projectId),
    enabled,
  });
}

export function useProjectResults(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.results(projectId),
    queryFn: () => api.getResults(projectId),
    enabled,
  });
}

export function useProjectFiles(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.files(projectId),
    queryFn: () => api.getFiles(projectId),
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
      if (runStatus && ["success", "failed", "cancelled", "reviewed"].includes(runStatus)) {
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
    refetchInterval: runStatus && ["success", "failed", "cancelled", "reviewed"].includes(runStatus) ? false : 4_000,
  });
}

export function useWorkspaceRefresh(projectId: string) {
  const queryClient = useQueryClient();
  return async function refreshWorkspace() {
    await Promise.all([
      queryClient.refetchQueries({ queryKey: queryKeys.project(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.projectEnvironment(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.chatSessions(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.workOrder(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.assetFlow(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.results(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.files(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: ["result-asset", projectId], type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.report(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.advancedGraph(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.advancedGit(projectId), type: "active" }),
      queryClient.refetchQueries({ queryKey: queryKeys.advancedProposals(projectId), type: "active" }),
    ]);
  };
}

export function useStartRunMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({
      cardId,
      workerType,
      profileId,
      pythonRuntime,
      rRuntime,
    }: {
      cardId: string;
      workerType?: string;
      profileId?: string;
      pythonRuntime?: string;
      rRuntime?: string;
    }) => api.startRun(projectId, cardId, workerType, profileId, pythonRuntime, rRuntime),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useCancelRunMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ runId, reason }: { runId: string; reason?: string }) => api.cancelRun(projectId, runId, reason),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useCleanupRunMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ runId, reason }: { runId: string; reason?: string }) => api.cleanupRun(projectId, runId, reason),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useResetCardRunStateMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({ cardId }: { cardId: string }) => api.resetCardRunState(projectId, cardId),
    onSuccess: async () => {
      await refresh();
    },
  });
}

export function useRerunCardMutation(projectId: string) {
  const refresh = useWorkspaceRefresh(projectId);
  return useMutation({
    mutationFn: ({
      cardId,
      workerType,
      profileId,
      pythonRuntime,
      rRuntime,
    }: {
      cardId: string;
      workerType?: string;
      profileId?: string;
      pythonRuntime?: string;
      rRuntime?: string;
    }) => api.rerunCard(projectId, cardId, workerType, profileId, pythonRuntime, rRuntime),
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

export function useProjectDataDirectoryExportHistory(projectId: string, enabled: boolean = true) {
  return useQuery({
    queryKey: [...queryKeys.project(projectId), "export-history"],
    queryFn: () => api.getProjectDataDirectoryExportHistory(projectId),
    enabled,
  });
}
