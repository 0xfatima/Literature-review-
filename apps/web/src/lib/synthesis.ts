import type {
  AppraisalAnswer,
  AppraisalFramework,
  Certainty,
  FieldSignal,
  StatementKind,
  SynthesisStatement,
} from "@/lib/types";

/** Labels spell out what the evidence supports; colour is never the only signal. */
export const CERTAINTY_LABELS: Record<Certainty, string> = {
  supported: "Supported by consistent evidence",
  uncertain: "Uncertain interpretation",
  conflicting: "Conflicting evidence",
  insufficient: "Insufficient evidence",
};

export const CERTAINTY_TONE: Record<Certainty, string> = {
  supported: "text-ok",
  uncertain: "text-mark",
  conflicting: "text-danger",
  insufficient: "text-muted",
};

export const KIND_LABELS: Record<StatementKind, string> = {
  finding: "Finding",
  agreement: "Agreement",
  disagreement: "Disagreement",
  gap: "Evidence gap",
  limitation: "Limitation",
  method_difference: "Methodological difference",
  conclusion: "Conclusion",
};

export const ORIGIN_LABELS: Record<SynthesisStatement["origin"], string> = {
  system: "Rule-based draft",
  ai: "AI draft",
  manual: "Written by reviewer",
};

export const PATTERN_LABELS: Record<FieldSignal["pattern"], string> = {
  no_evidence: "No reviewed values",
  single_study: "Single study",
  agreement: "Studies agree",
  disagreement: "Studies disagree",
  varied: "Reported differently",
};

export const humanize = (s: string | null | undefined): string => (s ? s.replace(/_/g, " ") : "—");

/** Gap counts with their reasons kept apart (missing evidence is never evidence of absence). */
export function gapSummary(signal: FieldSignal): string {
  const parts: string[] = [];
  if (signal.gaps.not_located.length)
    parts.push(`${signal.gaps.not_located.length} not found in processed text`);
  if (signal.gaps.unavailable.length) parts.push(`${signal.gaps.unavailable.length} without full text`);
  if (signal.gaps.ambiguous.length) parts.push(`${signal.gaps.ambiguous.length} ambiguous`);
  if (signal.gaps.not_reviewed.length) parts.push(`${signal.gaps.not_reviewed.length} not yet reviewed`);
  return parts.join(" · ");
}

/** Whether a draft can be accepted, and if not why (mirrors the server rule; the server decides). */
export function acceptBlocker(statements: SynthesisStatement[]): string | null {
  if (statements.some((s) => s.review === "unreviewed")) return "Review every statement first.";
  const kept = statements.filter((s) => s.review === "accepted" || s.review === "edited");
  if (kept.length === 0) return "Accept at least one statement.";
  if (kept.some((s) => s.stale)) return "Some accepted statements cite evidence that changed; refresh them.";
  return null;
}

/** Domains that still need a reviewer judgement (AI suggestions do not count). */
export function undecidedDomains(fw: AppraisalFramework, answers: Record<string, AppraisalAnswer>): string[] {
  return fw.domains.filter((d) => !answers[d.key]?.decided_by).map((d) => d.label);
}
