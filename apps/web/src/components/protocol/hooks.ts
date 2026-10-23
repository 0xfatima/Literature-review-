"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, json } from "@/lib/api";
import { isBusy } from "@/lib/protocol";
import type { ProtocolOverview, WorkflowState } from "@/lib/types";

export function useWorkflow(projectId: string, enabled = true) {
  return useQuery({
    queryKey: ["workflow", projectId],
    queryFn: () => apiFetch<WorkflowState>(`/projects/${projectId}/workflow`),
    enabled,
    // The worker usually finishes a step in well under a second; poll fast only while it owns the step.
    refetchInterval: (q) => (isBusy(q.state.data?.run) ? 700 : false),
  });
}

export function useProtocol(projectId: string, enabled = true) {
  return useQuery({
    queryKey: ["protocol", projectId],
    queryFn: () => apiFetch<ProtocolOverview>(`/projects/${projectId}/protocol`),
    enabled,
  });
}

/** Invalidate everything a workflow step can change. */
export function useRefreshReview(projectId: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["workflow", projectId] });
    qc.invalidateQueries({ queryKey: ["protocol", projectId] });
    qc.invalidateQueries({ queryKey: ["messages", projectId] });
    qc.invalidateQueries({ queryKey: ["conversations", projectId] });
    qc.invalidateQueries({ queryKey: ["jobs", projectId] });
    qc.invalidateQueries({ queryKey: ["project", projectId] });
  };
}

export interface AnswerBody {
  question_id: string;
  action: string;
  values?: Record<string, unknown>;
  expected_row_version?: number | null;
}

export function useAnswer(projectId: string) {
  const refresh = useRefreshReview(projectId);
  return useMutation({
    mutationFn: (body: AnswerBody) =>
      apiFetch<WorkflowState>(`/projects/${projectId}/workflow/resume`, { method: "POST", ...json(body) }),
    onSettled: refresh,
  });
}
