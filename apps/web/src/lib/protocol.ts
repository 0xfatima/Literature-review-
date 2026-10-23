/** Pure helpers for the protocol UI (unit-tested in protocol.test.ts). No network, no React. */
import type { Criterion, ProtocolContent, WorkflowRun } from "./types";

/** Workflow stages in their conceptual order; must match app/protocol/stages.py (contract test in the backend). */
export const WORKFLOW_STAGES = [
  "DRAFT",
  "PROTOCOL_REVIEW",
  "SEARCH",
  "RECORD_SCREENING",
  "FULL_TEXT_SCREENING",
  "EXTRACTION",
  "SYNTHESIS",
  "REPORT",
] as const;

export const SECTION_LABELS: Record<string, string> = {
  objective: "Objective",
  framework: "Question framework",
  elements: "Framework elements",
  questions: "Review questions",
  scope: "Scope",
  criteria: "Eligibility criteria",
};

/** True while the worker owns the next move (the UI polls and disables answering). */
export function isBusy(run: WorkflowRun | null | undefined): boolean {
  return run?.status === "queued" || run?.status === "running";
}

export function statusText(run: WorkflowRun): string {
  switch (run.status) {
    case "queued":
    case "running":
      return "Working on the next step…";
    case "awaiting_input":
      return "Waiting for your answer";
    case "idle":
      return run.stage === "SEARCH"
        ? "Protocol approved. Run the scholarly search from the Search tab."
        : "Paused";
    case "failed":
      return run.error_summary ?? "This step failed.";
    case "needs_attention":
      return run.error_summary ?? "The workflow needs attention.";
  }
}

/** "English, French" <-> ["English", "French"]; blanks and duplicates (case-insensitive) dropped. */
export function parseList(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[,;\n]/)) {
    const t = raw.trim();
    if (t && !seen.has(t.toLowerCase())) {
      seen.add(t.toLowerCase());
      out.push(t);
    }
  }
  return out;
}

export function parseYear(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) ? n : NaN;
}

/** Merge the draft's criteria with suggestions not already present (by stable key or same text). */
export function mergeCriteria(current: Criterion[], suggestions: Criterion[] = []): Criterion[] {
  const keys = new Set(current.map((c) => c.stable_key).filter(Boolean));
  const texts = new Set(current.map((c) => c.text.trim().toLowerCase()));
  return [
    ...current,
    ...suggestions.filter((s) => !keys.has(s.stable_key) && !texts.has(s.text.trim().toLowerCase())),
  ];
}

export function undecided(criteria: Criterion[]): number {
  return criteria.filter((c) => c.origin === "suggested" && c.state === "proposed").length;
}

/** Plain-language description of which stages a revision affects. Never claims results exist. */
export function impactText(affected: string[], labels: Record<string, string>): string {
  if (affected.length === 0) return "No material change: no stage needs to be rerun.";
  const names = affected.map((s) => labels[s] ?? s).join(", ");
  return `May need rerunning: ${names}. No search, screening or extraction results exist yet in this project, so nothing has to be redone now; these stages will use the new version.`;
}

export function emptyContent(): ProtocolContent {
  return {
    objective: null,
    framework: null,
    framework_source: null,
    framework_elements: [],
    questions: [],
    criteria: [],
    date_from: null,
    date_to: null,
    languages: [],
    study_designs: [],
    sources: [],
    search_strategy: null,
    extraction_plan: null,
    synthesis_plan: null,
  };
}

/** Question frameworks; must match app/protocol/frameworks.py (backend contract test). */
export const FRAMEWORKS: { key: string; label: string; elements: { key: string; label: string }[] }[] = [
  {
    key: "pico",
    label: "PICO",
    elements: [
      { key: "population", label: "Population" },
      { key: "intervention", label: "Intervention" },
      { key: "comparison", label: "Comparison" },
      { key: "outcome", label: "Outcome" },
    ],
  },
  {
    key: "peco",
    label: "PECO",
    elements: [
      { key: "population", label: "Population" },
      { key: "exposure", label: "Exposure" },
      { key: "comparator", label: "Comparator" },
      { key: "outcome", label: "Outcome" },
    ],
  },
  {
    key: "spider",
    label: "SPIDER",
    elements: [
      { key: "sample", label: "Sample" },
      { key: "phenomenon", label: "Phenomenon of interest" },
      { key: "design", label: "Design" },
      { key: "evaluation", label: "Evaluation" },
      { key: "research_type", label: "Research type" },
    ],
  },
  { key: "custom", label: "Other / custom", elements: [] },
  { key: "none", label: "Not applicable", elements: [] },
];

export function frameworkLabel(key: string | null): string {
  return FRAMEWORKS.find((f) => f.key === key)?.label ?? "Not chosen";
}

/** Element rows to edit: the framework's own elements (with saved values), or the saved custom rows. */
export function elementRows(framework: string | null, saved: FrameworkElementLike[]): FrameworkElementLike[] {
  const def = FRAMEWORKS.find((f) => f.key === framework);
  if (!def || framework === "none") return [];
  if (framework === "custom") return saved.length ? saved : [{ key: "", label: "", value: "" }];
  const values = new Map(saved.map((e) => [e.key, e.value]));
  return def.elements.map((e) => ({ key: e.key, label: e.label, value: values.get(e.key) ?? "" }));
}
export interface FrameworkElementLike {
  key: string;
  label: string;
  value: string | null;
}

/** Drop rows the user left blank before sending (the server rejects empty text). */
export function withText<T extends { text: string }>(items: T[]): T[] {
  return items.filter((x) => x.text.trim() !== "");
}
