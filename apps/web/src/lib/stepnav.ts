/** "Back" / "Next" between the main steps of a review, and whether the current step is complete. Pure. */
import type { ViewId } from "./steps";

/** The main path through a review; every other view belongs to one of these. */
export const FLOW: ViewId[] = [
  "protocol",
  "search",
  "screening",
  "fulltext",
  "extraction",
  "appraisal",
  "synthesis",
  "report",
];

const FLOW_OF: Partial<Record<ViewId, ViewId>> = {
  autopilot: "protocol",
  papers: "search",
  files: "fulltext",
  tables: "extraction",
  flow: "report",
  audit: "report",
};

/** The backend's workspace-overview stage that decides whether a view's step is complete. */
const STAGE_OF: Record<ViewId, string | null> = {
  protocol: "protocol",
  autopilot: "protocol",
  search: "search",
  papers: "search",
  screening: "screening",
  files: "full_text",
  fulltext: "full_text",
  extraction: "extraction",
  tables: "extraction",
  appraisal: "appraisal",
  synthesis: "synthesis",
  report: "report",
  flow: "report",
  audit: null,
};

export interface StageLike {
  key: string;
  status: string;
  detail: string;
}

export interface StepNavState {
  prev: ViewId | null;
  next: ViewId | null;
  /** The step is finished: Next is enabled (and shown in green). Views without a finish line count as complete. */
  complete: boolean;
  status: string | null;
  detail: string;
}

export function flowView(view: ViewId): ViewId {
  return FLOW_OF[view] ?? view;
}

export function stepNav(view: ViewId, stages: StageLike[] | undefined): StepNavState {
  const base = flowView(view);
  const i = FLOW.indexOf(base);
  const key = STAGE_OF[view];
  const stage = key ? stages?.find((s) => s.key === key) : undefined;
  return {
    prev: i > 0 ? (FLOW[i - 1] ?? null) : null,
    next: i >= 0 && i < FLOW.length - 1 ? (FLOW[i + 1] ?? null) : null,
    complete: key === null ? true : stage?.status === "done",
    status: stage?.status ?? null,
    detail: stage?.detail ?? "",
  };
}
