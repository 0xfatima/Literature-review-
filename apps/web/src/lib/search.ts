/** Pure helpers for the search and papers UI (no network, no React). */
import type { SearchQueryStatus, SearchRun, SearchRunStatus } from "./types";

export const RUN_STATUS_LABELS: Record<SearchRunStatus, string> = {
  queued: "Queued",
  running: "Searching…",
  completed: "Complete: every source finished",
  partial: "Incomplete: some sources did not finish",
  failed: "Failed: no source finished",
  cancelled: "Cancelled",
};

export const QUERY_STATUS_LABELS: Record<SearchQueryStatus, string> = {
  pending: "Waiting",
  running: "Running",
  completed: "Complete",
  capped: "Stopped at record limit (more results exist)",
  partial: "Partly retrieved, then failed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const MATCH_BASIS_LABELS: Record<string, string> = {
  doi: "Matched by DOI",
  pmid: "Matched by PMID",
  arxiv_id: "Matched by arXiv id",
  openalex_id: "Matched by OpenAlex id",
  title_author_year: "Matched by title, first author and year",
  new: "First record of this paper",
};

export const EXPRESSION_SOURCE_LABELS: Record<string, string> = {
  search_strategy: "From the protocol's search strategy",
  framework_elements: "Built from the protocol's framework elements",
  objective: "Keywords from the protocol objective (review before running)",
  topic: "Keywords from the project topic (review before running)",
  user_edited: "Edited by you (the protocol-derived query is kept for the record)",
  none: "No query could be derived; write one",
};

export function isActiveRun(run: Pick<SearchRun, "status"> | null | undefined): boolean {
  return run?.status === "queued" || run?.status === "running";
}

export function canRetry(run: SearchRun): boolean {
  return (
    run.is_current_protocol &&
    (run.status === "partial" || run.status === "failed" || run.status === "cancelled") &&
    run.queries.some((q) => ["pending", "running", "failed", "partial", "cancelled"].includes(q.status))
  );
}

/** Shown on runs the user must not read as complete. */
export function incompleteWarning(run: SearchRun): string | null {
  if (run.status === "completed") return null;
  if (isActiveRun(run)) return null;
  const capped = run.queries.filter((q) => q.status === "capped").map((q) => q.provider_label);
  const broken = run.queries
    .filter((q) => ["failed", "partial", "cancelled", "pending"].includes(q.status))
    .map((q) => q.provider_label);
  const parts: string[] = [];
  if (broken.length) parts.push(`not finished for ${broken.join(", ")}`);
  if (capped.length) parts.push(`more results exist than were retrieved for ${capped.join(", ")}`);
  return parts.length ? `This search is not complete: ${parts.join("; ")}.` : null;
}

export function fmtDate(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function doiUrl(doi: string): string {
  return `https://doi.org/${encodeURI(doi)}`;
}
