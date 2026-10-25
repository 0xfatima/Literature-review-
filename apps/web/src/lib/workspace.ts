import { ApiError } from "@/lib/api";
import type { Job } from "@/lib/types";

export type JobState = "queued" | "running" | "completed" | "failed" | "cancelled";

/** Backend job statuses mapped to the five states the interface distinguishes. */
export function jobState(j: Pick<Job, "status" | "cancel_requested">): JobState {
  if (j.status === "queued") return "queued";
  if (j.status === "running" || j.status === "awaiting_input") return "running";
  if (j.status === "succeeded") return "completed";
  if (j.status === "cancelled") return "cancelled";
  return "failed";
}

export const JOB_STATE_LABELS: Record<JobState, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const JOB_STATE_TONE: Record<JobState, string> = {
  queued: "text-muted",
  running: "text-mark",
  completed: "text-ok",
  failed: "text-danger",
  cancelled: "text-muted",
};

/** Jobs that reached a terminal state since the previous poll (for notifications). */
export function newlyFinished(previous: Job[], current: Job[]): Job[] {
  const active = (j: Job) => j.status === "queued" || j.status === "running" || j.status === "awaiting_input";
  const wasActive = new Set(previous.filter(active).map((j) => j.id));
  return current.filter((j) => wasActive.has(j.id) && !active(j));
}

export type StageStatus = "not_started" | "in_progress" | "done" | "attention";

export const STAGE_STATUS_LABELS: Record<StageStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  done: "Done",
  attention: "Needs attention",
};

/** Understandable messages for failed requests; the server's own message wins when it sent one. */
export function describeError(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "Your session has ended. Sign in again to continue.";
    if (e.status === 403) return "You do not have permission to do this.";
    if (e.status === 404) return e.message || "This item no longer exists or is not in this project.";
    if (e.status === 429) return e.message || "Too many requests. Wait a moment and try again.";
    if (e.status >= 500 && e.status !== 503)
      return "The server had a problem. Your work is saved; try again shortly.";
    return e.message || fallback;
  }
  if (e instanceof TypeError) return "Network error. Check your connection and try again.";
  return fallback;
}

/** Split text into [before, match, after] around the first case-insensitive occurrence of `needle`. */
export function splitHighlight(
  text: string,
  needle: string | null | undefined,
): [string, string, string] | null {
  if (!needle) return null;
  const i = text.toLowerCase().indexOf(needle.toLowerCase());
  if (i < 0) return null;
  return [text.slice(0, i), text.slice(i, i + needle.length), text.slice(i + needle.length)];
}

/** sessionStorage helpers for unsaved drafts (per browser tab; never sent anywhere). */
export function loadDraft(key: string): string {
  try {
    return window.sessionStorage.getItem(`draft:${key}`) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(key: string, value: string): void {
  try {
    if (value) window.sessionStorage.setItem(`draft:${key}`, value);
    else window.sessionStorage.removeItem(`draft:${key}`);
  } catch {
    /* storage unavailable: the draft lives in memory only */
  }
}
