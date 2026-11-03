"use client";
/** Live status of every review stage, computed by the backend from stored data (workspace overview). */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { StageStatus } from "@/lib/workspace";

export interface StageInfo {
  key: string;
  label: string;
  tab: string;
  status: StageStatus;
  detail: string;
}
export interface WorkspaceOverview {
  stages: StageInfo[];
  current: string;
  active_jobs: number;
  failed_jobs: number;
}

export function useWorkspaceOverview(projectId: string) {
  return useQuery({
    queryKey: ["workspace", projectId, "overview"],
    queryFn: () => apiFetch<WorkspaceOverview>(`/projects/${projectId}/workspace/overview`),
    refetchInterval: (q) => ((q.state.data?.active_jobs ?? 0) > 0 ? 5000 : 30_000),
  });
}
