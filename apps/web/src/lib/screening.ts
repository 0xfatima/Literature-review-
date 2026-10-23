/** Pure helpers for the screening UI (no network, no React). Colour is never the only signal: every band has a
 * text label and a filled-dots marker, and every decision has a text label. */
import type { Band, HumanDecision } from "./types";

export const BANDS: Band[] = ["high", "moderate_high", "possible", "low", "insufficient", "not_assessed"];

export const BAND_LABELS: Record<Band, string> = {
  high: "High relevance",
  moderate_high: "Moderate-to-high relevance",
  possible: "Possible relevance",
  low: "Low relevance",
  insufficient: "Insufficient metadata",
  not_assessed: "Not yet assessed",
};

/** Text colour + border classes. Greens for relevance, neutral grey for low/unknown (spec Phase 5). */
export const BAND_STYLES: Record<Band, string> = {
  high: "border-[#4ADE80] text-[#4ADE80]",
  moderate_high: "border-[#A3E635] text-[#A3E635]",
  possible: "border-[#BBF7D0] text-[#BBF7D0]",
  low: "border-line text-muted",
  insufficient: "border-line text-muted",
  not_assessed: "border-line text-muted",
};

/** A shape cue that survives greyscale and colour-blindness. */
export const BAND_DOTS: Record<Band, string> = {
  high: "●●●",
  moderate_high: "●●○",
  possible: "●○○",
  low: "○○○",
  insufficient: "–",
  not_assessed: "–",
};

export const DECISION_LABELS: Record<HumanDecision | "cleared", string> = {
  include: "Include",
  exclude: "Exclude",
  uncertain: "Uncertain",
  cleared: "No decision",
};

export const SUGGESTION_LABELS: Record<HumanDecision, string> = {
  include: "AI suggests: include",
  exclude: "AI suggests: exclude",
  uncertain: "AI suggests: uncertain",
};

export const JUDGEMENT_LABELS = { met: "met", not_met: "not met", unclear: "unclear" } as const;

export const FIELD_LABELS = { title: "title", keywords: "keywords", abstract: "abstract" } as const;

export function progressPercent(done: number, total: number): number {
  return total > 0 ? Math.round((100 * done) / total) : 0;
}
