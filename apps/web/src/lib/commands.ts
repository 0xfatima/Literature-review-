/** Chat commands ("run the search", "go to screening", "next step"), interpreted by POST /assist/command. */
import type { ViewId } from "./steps";

export interface ChatCommand {
  action: string;
  reply?: string;
  view?: ViewId | null;
  params?: Record<string, unknown>;
  /** The ordinary API request behind the action; sent only after the user confirms. */
  request?: { method: string; path: string; body: Record<string, unknown> } | null;
  confirm?: boolean;
  source?: "rules" | "ai";
}

export const COMMAND_EXAMPLES = ["run the search", "go to screening", "next step", "assess relevance"];

/** Asks open panels to refresh after a chat command changed their data. */
export function announceChange(view: string | null | undefined): void {
  if (typeof window !== "undefined")
    window.dispatchEvent(new CustomEvent("srs:changed", { detail: { view } }));
}
