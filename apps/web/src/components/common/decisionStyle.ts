/** One colour per decision everywhere in the app: green = include / accept, red = exclude / reject,
 *  olive = uncertain. A chosen option is filled; the others are a soft tint of the same colour. */
export type DecisionTone = "include" | "exclude" | "uncertain";

const FILLED: Record<DecisionTone, string> = {
  include: "btn-accept",
  exclude: "btn-reject",
  uncertain: "btn-uncertain",
};
const SOFT: Record<DecisionTone, string> = {
  include: "btn-accept-soft",
  exclude: "btn-reject-soft",
  uncertain: "btn-uncertain-soft",
};

export function decisionButton(tone: DecisionTone, chosen = false): string {
  return chosen ? FILLED[tone] : SOFT[tone];
}

export const DECISION_BADGE: Record<DecisionTone, string> = {
  include: "badge-accept",
  exclude: "badge-reject",
  uncertain: "badge-uncertain",
};
