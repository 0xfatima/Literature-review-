"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { stageLabel } from "@/lib/stages";
import type { Job, JobList, Project } from "@/lib/types";
import { useStatus } from "./StatusContext";

const JOB_LABELS: Record<string, string> = {
  "system.ping": "Worker check",
  "workflow.advance": "Review workflow step",
};
// Mirrors app/jobs/fsm.JOB_TYPES[...]["cancellable"]: the server refuses to cancel these.
const NOT_CANCELLABLE = new Set(["workflow.advance"]);
const jobLabel = (j: Job) => JOB_LABELS[j.type] ?? j.type;

/** Bottom bar: workflow stage, live background jobs (real data from /jobs), save state and errors. */
export default function StatusBar({ project }: { project: Project }) {
  const qc = useQueryClient();
  const { save, error, reportError } = useStatus();
  const jobs = useQuery({
    queryKey: ["jobs", project.id, "active"],
    queryFn: () => apiFetch<JobList>(`/jobs?project_id=${project.id}&active=true`),
    // Poll quickly only while something is running; otherwise check occasionally.
    refetchInterval: (q) => ((q.state.data?.items.length ?? 0) > 0 ? 1500 : 10_000),
  });
  const cancel = useMutation({
    mutationFn: (j: Job) => apiFetch<Job>(`/jobs/${j.id}/cancel`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["jobs", project.id] }),
    onError: () => reportError("Could not cancel the job."),
  });

  const active = jobs.data?.items ?? [];
  return (
    <>
      <span>
        Stage: <span className="text-ink">{stageLabel(project.status)}</span>
      </span>
      {project.archived_at && <span className="text-mark">Archived, read-only</span>}

      <span aria-live="polite" className="flex items-center gap-3">
        {jobs.isError && <span className="text-danger">Job status unavailable</span>}
        {jobs.isSuccess && active.length === 0 && <span>No background jobs running</span>}
        {active.map((j) => (
          <span key={j.id} className="flex items-center gap-2" data-testid="active-job">
            <span className="text-ink">{jobLabel(j)}</span>
            <span>
              {j.status === "queued" ? "Queued" : j.cancel_requested ? "Cancelling" : `${j.progress}%`}
            </span>
            <progress
              className="h-1.5 w-20 accent-mark"
              max={100}
              value={j.progress}
              aria-label={`${jobLabel(j)} progress`}
            />
            {!j.cancel_requested && !NOT_CANCELLABLE.has(j.type) && (
              <button type="button" className="text-link hover:underline" onClick={() => cancel.mutate(j)}>
                Cancel
              </button>
            )}
          </span>
        ))}
      </span>

      <span className="ml-auto flex items-center gap-3" role="status">
        {error && (
          <span className="flex items-center gap-1 text-danger">
            <span>Error: {error}</span>
            <button type="button" className="underline" onClick={() => reportError(null)}>
              Dismiss
            </button>
          </span>
        )}
        {save.kind === "saving" && <span>Saving…</span>}
        {save.kind === "saved" && <span>Saved {save.at.toLocaleTimeString()}</span>}
        {save.kind === "failed" && <span className="text-danger">Failed to save: {save.message}</span>}
      </span>
    </>
  );
}
