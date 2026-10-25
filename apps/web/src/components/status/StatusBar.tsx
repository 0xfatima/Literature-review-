"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import ProgressIndicator from "@/components/common/ProgressIndicator";
import { useHasUnsavedChanges } from "@/components/workspace/WorkspaceContext";
import { JOB_STATE_LABELS, jobState, newlyFinished } from "@/lib/workspace";
import { apiFetch } from "@/lib/api";
import { stageLabel } from "@/lib/stages";
import type { Job, JobList, Project } from "@/lib/types";
import { useStatus } from "./StatusContext";

const JOB_LABELS: Record<string, string> = {
  "system.ping": "Worker check",
  "workflow.advance": "Review workflow step",
  "search.run": "Scholarly search",
  "relevance.assess": "Relevance assessment",
  "document.process": "Reading PDF",
  "extraction.run": "Evidence extraction",
  "appraisal.suggest": "Appraisal suggestions",
  "synthesis.draft": "Synthesis draft",
  "pipeline.advance": "Pipeline step",
};
// Mirrors app/jobs/fsm.JOB_TYPES[...]["cancellable"]: the server refuses to cancel these.
const NOT_CANCELLABLE = new Set([
  "workflow.advance",
  "document.process",
  "synthesis.draft",
  "pipeline.advance",
]);
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
  const recent = useQuery({
    queryKey: ["jobs", project.id, "recent"],
    queryFn: () => apiFetch<JobList>(`/jobs?project_id=${project.id}&limit=15`),
    refetchInterval: active.length > 0 ? 2000 : 15_000,
  });
  const [open, setOpen] = useState(false);
  const [notices, setNotices] = useState<{ id: string; text: string; failed: boolean }[]>([]);
  const previous = useRef<Job[] | null>(null);
  const unsaved = useHasUnsavedChanges();
  useEffect(() => {
    const items = recent.data?.items;
    if (!items) return;
    if (previous.current) {
      const done = newlyFinished(previous.current, items);
      if (done.length) {
        // A finished job changes stored data: refresh every panel of this project so chat and panels agree.
        qc.invalidateQueries({ predicate: (q) => q.queryKey[1] === project.id && q.queryKey[0] !== "jobs" });
        setNotices((n) =>
          [
            ...done.map((j) => ({
              id: j.id,
              text: `${jobLabel(j)}: ${JOB_STATE_LABELS[jobState(j)].toLowerCase()}${
                j.error_summary ? ` — ${j.error_summary}` : ""
              }`,
              failed: jobState(j) === "failed",
            })),
            ...n,
          ].slice(0, 3),
        );
      }
    }
    previous.current = items;
  }, [recent.data, qc, project.id]);
  const failedCount = (recent.data?.items ?? []).filter((j) => jobState(j) === "failed").length;
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

      <span className="relative">
        <button
          type="button"
          className="text-link hover:underline"
          aria-expanded={open}
          aria-controls="jobs-menu"
          onClick={() => setOpen((v) => !v)}
        >
          Jobs{failedCount ? ` (${failedCount} failed)` : ""}
        </button>
        {open && (
          <div
            id="jobs-menu"
            role="dialog"
            aria-label="Recent background jobs"
            className="absolute bottom-6 left-0 z-40 w-[min(32rem,90vw)] rounded border border-line bg-panel p-3 shadow"
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
            }}
          >
            <p className="mb-2 text-xs text-muted">
              Recent jobs for this project. Failed jobs keep the work completed before the failure; start them
              again from their panel.
            </p>
            <ul className="flex max-h-72 flex-col gap-2 overflow-y-auto text-xs">
              {(recent.data?.items ?? []).map((j) => (
                <li key={j.id} className="border-b border-line pb-1">
                  <ProgressIndicator label={jobLabel(j)} job={j} />
                  <span className="block text-2xs text-muted">
                    Started {new Date(j.created_at).toLocaleString()}
                    {j.finished_at && ` · finished ${new Date(j.finished_at).toLocaleTimeString()}`}
                  </span>
                </li>
              ))}
              {recent.data && recent.data.items.length === 0 && <li className="text-muted">No jobs yet.</li>}
            </ul>
            <button type="button" className="btn-ghost mt-2 text-xs" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
        )}
      </span>
      <span aria-live="polite" className="flex items-center gap-2">
        {notices.map((n) => (
          <span key={n.id} className={n.failed ? "text-danger" : "text-ok"}>
            {n.text}
            <button
              type="button"
              className="ml-1 underline"
              onClick={() => setNotices((l) => l.filter((x) => x.id !== n.id))}
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </span>
        ))}
      </span>

      <span className="ml-auto flex items-center gap-3" role="status">
        {unsaved && <span className="text-mark">Unsaved edits</span>}
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
