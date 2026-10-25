"use client";
import { JOB_STATE_LABELS, JOB_STATE_TONE, jobState } from "@/lib/workspace";
import type { Job } from "@/lib/types";

/** One background job: its state in words (never colour alone), real progress, and its message or error. */
export default function ProgressIndicator({
  label,
  job,
  compact = false,
}: {
  label: string;
  job: Job;
  compact?: boolean;
}) {
  const state = jobState(job);
  const live = state === "queued" || state === "running";
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-2" data-testid="job-progress" data-state={state}>
      <span className="text-ink">{label}</span>
      <span className={JOB_STATE_TONE[state]}>
        {job.cancel_requested && live ? "Cancelling" : JOB_STATE_LABELS[state]}
        {state === "running" && ` ${job.progress}%`}
      </span>
      {live && (
        <progress
          className="h-1.5 w-20 accent-mark"
          max={100}
          // A queued job has no progress yet: an indeterminate bar, not a fake percentage.
          value={state === "queued" ? undefined : job.progress}
          aria-label={`${label} progress`}
        />
      )}
      {!compact && job.progress_message && live && <span className="text-muted">{job.progress_message}</span>}
      {!compact && state === "failed" && job.error_summary && (
        <span className="text-danger">{job.error_summary}</span>
      )}
    </span>
  );
}
