/** Project statuses from the backend (db/models.PROJECT_STATUSES) with user-facing labels. */
export const STAGES = [
  { id: "setup", label: "Setup" },
  { id: "searching", label: "Searching" },
  { id: "screening", label: "Screening" },
  { id: "awaiting_pdfs", label: "Awaiting PDFs" },
  { id: "extracting", label: "Extracting" },
  { id: "synthesizing", label: "Synthesizing" },
  { id: "draft_ready", label: "Draft ready" },
  { id: "completed", label: "Completed" },
] as const;

/** "needs_attention" is a flag on top of the pipeline, not a step in it. */
export const ATTENTION = { id: "needs_attention", label: "Needs attention" } as const;

export function stageLabel(status: string): string {
  if (status === ATTENTION.id) return ATTENTION.label;
  return STAGES.find((s) => s.id === status)?.label ?? status;
}

/** Index in the pipeline, or -1 when the status is not a pipeline step (e.g. needs_attention). */
export function stageIndex(status: string): number {
  return STAGES.findIndex((s) => s.id === status);
}
