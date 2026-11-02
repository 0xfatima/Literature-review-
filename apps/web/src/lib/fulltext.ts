/** Full-text screening and study grouping: labels and rules shared by the panels. Pure, no React. */
import type { FtReason, FtStatus, ReportBrief } from "./types";

export const FT_STATUS_ORDER: FtStatus[] = ["awaiting", "uncertain", "not_retrieved", "excluded", "included"];

/** Status words are always spelled out; colour is never the only signal. */
export const FT_STATUS_LABELS: Record<FtStatus, string> = {
  awaiting: "Awaiting full-text decision",
  uncertain: "Uncertain",
  included: "Included",
  excluded: "Excluded",
  not_retrieved: "Full text not retrieved",
};

/** What the app actually holds for this paper, never what a provider claims. */
export const FULL_TEXT_LABELS: Record<ReportBrief["full_text_status"], string> = {
  not_obtained: "No PDF uploaded",
  uploaded: "PDF uploaded, not read yet",
  parsed: "PDF text available",
  failed: "PDF could not be read",
};

export type FtDecision = "include" | "exclude" | "uncertain";

/** Mirrors the backend: a full-text exclusion always needs a reason, and "other" needs a note. */
export function decisionProblem(decision: FtDecision, reasonCode: string, note: string): string | null {
  if (decision !== "exclude") return null;
  if (!reasonCode) return "Choose a reason for excluding this report.";
  if (reasonCode === "other" && !note.trim()) return "Explain the reason in the note.";
  return null;
}

/** Reasons grouped for a <select>: protocol criteria first, then the standard methodological reasons. */
export function groupReasons(reasons: FtReason[]): { label: string; options: FtReason[] }[] {
  const groups: { label: string; key: FtReason["group"] }[] = [
    { label: "Your exclusion criteria", key: "exclusion_criterion" },
    { label: "Your inclusion criteria (not met)", key: "inclusion_not_met" },
    { label: "Standard reasons", key: "generic" },
  ];
  return groups
    .map((g) => ({ label: g.label, options: reasons.filter((r) => r.group === g.key) }))
    .filter((g) => g.options.length > 0);
}

/** A study groups two or more reports; one report is already counted as its own study. */
export function groupingProblem(selected: string[], shortName: string): string | null {
  if (selected.length < 2) return "Select at least two reports of the same study.";
  if (shortName.trim().length > 200) return "The study name is too long (200 characters at most).";
  return null;
}

export function citeLabel(p: Pick<ReportBrief, "first_author" | "author_count" | "year">): string {
  const who = p.first_author
    ? p.author_count > 1
      ? `${p.first_author} et al.`
      : p.first_author
    : "Unknown author";
  return p.year ? `${who} (${p.year})` : who;
}
