import type { PipelineRunView } from "@/lib/types";

export const AUTONOMY_LABELS: Record<string, string> = {
  search: "Run the protocol's search without asking",
  screening: "Rank records by relevance (advisory only)",
  extraction: "Start AI evidence extraction (sends document text; consent still required)",
  appraisal: "Ask AI for appraisal suggestions (suggestions only)",
  synthesis: "Draft the synthesis from reviewed evidence",
  synthesis_ai: "…including AI-drafted statements",
  report: "Generate the report draft from stored data",
};

export const OPTION_LABELS: Record<string, string> = {
  approve: "Approve and run",
  continue: "I have done this — check again",
  skip: "Skip this stage",
  stop: "Stop the run",
  retry: "Retry",
  extend: "Allow another budget",
};

export const EVENT_LABELS: Record<string, string> = {
  run_created: "Run created",
  run_started: "Run started",
  rerun: "Rerun from an earlier stage",
  stage_completed: "Stage completed",
  stage_skipped: "Stage skipped",
  job_dispatched: "Background job started",
  job_finished: "Background job finished",
  technical_retry: "Automatic retry scheduled (technical failure)",
  manual_retry: "Retried by a person",
  approval_requested: "Waiting for your decision",
  approval_given: "Decision recorded",
  answer: "Answer applied",
  paused: "Paused",
  resumed: "Resumed",
  recovered: "Recovered from the last checkpoint",
  cancelled: "Cancelled",
  superseded: "Superseded by a rerun",
  decisions_confirmed: "Decisions confirmed under the new protocol",
};

export type StageState = "done" | "skipped" | "current" | "todo";

/** Per-stage state of a run, from its stored outputs and position. */
export function stageStates(run: PipelineRunView): Record<string, StageState> {
  const out: Record<string, StageState> = {};
  const order = run.stages.map((s) => s.key);
  const started = order.indexOf(run.start_stage);
  for (const [i, s] of order.entries()) {
    if (run.counters.skipped?.includes(s)) out[s] = "skipped";
    else if (run.outputs[s]) out[s] = "done";
    else if (s === run.stage && !["completed", "cancelled", "superseded"].includes(run.status))
      out[s] = "current";
    else out[s] = i < started ? "done" : "todo";
  }
  return out;
}

export const isLive = (run: PipelineRunView | null | undefined): boolean =>
  !!run && ["queued", "running", "waiting"].includes(run.status);
