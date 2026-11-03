/** The review as eight main steps (the project's own status, so there is one source of truth for "where we
 *  are"), each holding the views that belong to it. Pure and unit-tested; no React, no network. */
import { ATTENTION, STAGES } from "./stages";

export const VIEWS = [
  { id: "protocol", label: "Protocol", hint: "Question, framework, scope and eligibility criteria" },
  { id: "autopilot", label: "Automation", hint: "Choose which steps run by themselves" },
  { id: "search", label: "Search", hint: "Run and refine the scholarly search" },
  { id: "papers", label: "Papers", hint: "Every record the searches found" },
  { id: "screening", label: "Screening", hint: "Include or exclude by title and abstract" },
  { id: "files", label: "Files", hint: "Upload full-text PDFs" },
  { id: "fulltext", label: "Full text", hint: "Assess included papers in full" },
  { id: "extraction", label: "Extraction form", hint: "Fields to extract from each study" },
  { id: "tables", label: "Evidence table", hint: "Extracted values with their sources" },
  { id: "appraisal", label: "Appraisal", hint: "Risk of bias and quality" },
  { id: "synthesis", label: "Synthesis", hint: "Combine the findings" },
  { id: "flow", label: "PRISMA flow", hint: "Counts at every stage" },
  { id: "report", label: "Report", hint: "Draft, check and export" },
  { id: "audit", label: "Audit log", hint: "Every change, who made it and when" },
] as const;

export type ViewId = (typeof VIEWS)[number]["id"];
export type StepId = (typeof STAGES)[number]["id"];

export interface ReviewStep {
  id: StepId;
  label: string;
  /** What the person does in this step, in a few words. */
  summary: string;
  views: ViewId[];
}

const VIEWS_OF: Record<StepId, ViewId[]> = {
  setup: ["protocol", "autopilot"],
  searching: ["search", "papers"],
  screening: ["screening"],
  awaiting_pdfs: ["files", "fulltext"],
  extracting: ["extraction", "tables", "appraisal"],
  synthesizing: ["synthesis"],
  draft_ready: ["report", "flow"],
  completed: ["audit"],
};

const SUMMARY: Record<StepId, string> = {
  setup: "Agree the protocol in the chat",
  searching: "Find candidate papers",
  screening: "Decide which records are relevant",
  awaiting_pdfs: "Collect and assess full texts",
  extracting: "Pull out the evidence",
  synthesizing: "Bring the findings together",
  draft_ready: "Review the draft report",
  completed: "Review finished",
};

export const REVIEW_STEPS: ReviewStep[] = STAGES.map((s) => ({
  id: s.id,
  label: s.label,
  summary: SUMMARY[s.id],
  views: VIEWS_OF[s.id],
}));

export function isViewId(v: string | null | undefined): v is ViewId {
  return !!v && VIEWS.some((x) => x.id === v);
}

export function viewLabel(id: ViewId): string {
  return VIEWS.find((v) => v.id === id)?.label ?? id;
}

export function stepOfView(view: string): ReviewStep | null {
  return REVIEW_STEPS.find((s) => (s.views as string[]).includes(view)) ?? null;
}

export type StepState = "done" | "current" | "todo" | "attention";

export const STEP_STATE_LABELS: Record<StepState, string> = {
  done: "Done",
  current: "Current step",
  todo: "Not started",
  attention: "Needs attention",
};

/** Index of the current main step. For "needs_attention" the project status does not say which step stalled,
 *  so the view the backend flags (workspace overview) decides; -1 when nothing points anywhere. */
export function currentStepIndex(projectStatus: string, attentionView?: string | null): number {
  if (projectStatus === ATTENTION.id) {
    const step = attentionView ? stepOfView(attentionView) : null;
    return step ? REVIEW_STEPS.indexOf(step) : -1;
  }
  return REVIEW_STEPS.findIndex((s) => s.id === projectStatus);
}

/** State of every main step: earlier ones done, the current one current (or attention), later ones to do.
 *  "completed" counts as done itself: nothing is left to do. */
export function stepStates(projectStatus: string, attentionView?: string | null): StepState[] {
  const current = currentStepIndex(projectStatus, attentionView);
  const attention = projectStatus === ATTENTION.id;
  return REVIEW_STEPS.map((s, i) => {
    if (i < current) return "done";
    if (i === current) {
      if (attention) return "attention";
      return s.id === "completed" ? "done" : "current";
    }
    return "todo";
  });
}

/** Background job types mapped to the view that shows their result (for "Open …" in notifications). */
export const JOB_VIEW: Record<string, ViewId> = {
  "search.run": "search",
  "relevance.assess": "screening",
  "document.process": "files",
  "extraction.run": "tables",
  "appraisal.suggest": "appraisal",
  "synthesis.draft": "synthesis",
  "pipeline.advance": "autopilot",
};

export const JOB_LABELS: Record<string, string> = {
  "system.ping": "Worker check",
  "workflow.advance": "Protocol step",
  "search.run": "Scholarly search",
  "relevance.assess": "Relevance assessment",
  "document.process": "Reading PDF",
  "extraction.run": "Evidence extraction",
  "appraisal.suggest": "Appraisal suggestions",
  "synthesis.draft": "Synthesis draft",
  "pipeline.advance": "Automation step",
};

export function jobLabel(type: string): string {
  return JOB_LABELS[type] ?? type;
}

/** Which open view to show after closing `closing`: the right neighbour, else the left one, else none. */
export function nextAfterClose(open: ViewId[], closing: ViewId, active: ViewId | null): ViewId | null {
  if (closing !== active) return active;
  const i = open.indexOf(closing);
  const rest = open.filter((v) => v !== closing);
  if (!rest.length) return null;
  return rest[Math.min(i, rest.length - 1)] ?? null;
}

/** Filter commands for the command palette: every word must appear, in any order (case-insensitive). */
export function matchesQuery(text: string, query: string): boolean {
  const hay = text.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
}

/** Palette ranking: label starts with the query, then label contains it, then keyword-only matches. */
export function rankQuery(label: string, query: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const l = label.toLowerCase();
  if (l.startsWith(q)) return 0;
  if (l.includes(q)) return 1;
  return 2;
}
