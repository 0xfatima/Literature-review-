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
/** Relevance colours, light tints that stay readable in light and dark mode: green = relevant, amber = maybe,
 *  light red = low. The label and the dots carry the same meaning, so colour is never the only signal. */
export const BAND_STYLES: Record<Band, string> = {
  high: "border-ok bg-ok/20 text-ok font-semibold",
  moderate_high: "border-ok/60 bg-ok/10 text-ok",
  possible: "border-warn/60 bg-warn/10 text-warn",
  low: "border-danger/50 bg-danger/10 text-danger",
  insufficient: "border-line bg-raised text-muted",
  not_assessed: "border-line bg-raised text-muted",
};

/** A coloured left edge on each record, so relevance can be scanned down the list at a glance. */
export const BAND_EDGES: Record<Band, string> = {
  high: "border-l-4 border-l-ok",
  moderate_high: "border-l-4 border-l-ok/50",
  possible: "border-l-4 border-l-warn/70",
  low: "border-l-4 border-l-danger/60",
  insufficient: "border-l-4 border-l-line",
  not_assessed: "border-l-4 border-l-line",
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

/** Job errors are written for logs; these are the ones people actually meet, in plain words. */
export function friendlyAiError(e: string): string {
  if (/worker lost/i.test(e))
    return "The AI screening was interrupted (the app restarted while it ran). Start it again to continue; nothing already done is lost.";
  return e;
}
