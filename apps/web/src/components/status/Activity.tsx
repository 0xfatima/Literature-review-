"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";
import ProgressIndicator from "@/components/common/ProgressIndicator";
import { useWorkflow } from "@/components/protocol/hooks";
import Icon, { Spinner } from "@/components/ui/Icon";
import { PopoverPanel, usePopover } from "@/components/ui/Popover";
import { type WorkspaceOverview, useWorkspaceOverview } from "@/components/workspace/overview";
import { apiFetch } from "@/lib/api";
import { SECTION_LABELS, isBusy } from "@/lib/protocol";
import { stageLabel } from "@/lib/stages";
import {
  JOB_VIEW,
  REVIEW_STEPS,
  type ViewId,
  currentStepIndex,
  jobLabel,
  stepOfView,
  viewLabel,
} from "@/lib/steps";
import type { Job, JobList, Project, WorkflowRun } from "@/lib/types";
import { JOB_STATE_LABELS, jobState, newlyFinished } from "@/lib/workspace";
import { useStatus } from "./StatusContext";

// Mirrors app/jobs/fsm.JOB_TYPES[...]["cancellable"]: the server refuses to cancel these.
const NOT_CANCELLABLE = new Set([
  "workflow.advance",
  "document.process",
  "synthesis.draft",
  "pipeline.advance",
]);

interface ActivityApi {
  project: Project;
  active: Job[];
  recent: Job[];
  jobsError: boolean;
  run: WorkflowRun | null;
  overview: WorkspaceOverview | undefined;
  /** The view the backend flags as needing attention or in progress (workspace overview). */
  attentionView: string | null;
  cancel: (j: Job) => void;
  openView: (v: ViewId) => void;
  openChat: (conversationId?: string | null) => void;
  openSteps: () => void;
}

const Ctx = createContext<ActivityApi | null>(null);

/** Plain name of the protocol question waiting in the chat. */
function questionName(run: WorkflowRun | null): string {
  const kind = run?.pending_question?.kind;
  if (!kind) return "a protocol question";
  return kind === "review" ? "review and approve the protocol" : (SECTION_LABELS[kind] ?? kind).toLowerCase();
}

/** Watches background jobs and the protocol workflow for one project. Everything that finishes, fails or
 *  waits for the person becomes a notification, with a button that goes straight to the right place. */
export function ActivityProvider({
  project,
  openView,
  openChat,
  openSteps,
  children,
}: {
  project: Project;
  openView: (v: ViewId) => void;
  openChat: (conversationId?: string | null) => void;
  openSteps: () => void;
  children: ReactNode;
}) {
  const qc = useQueryClient();
  const { notify, reportError } = useStatus();
  const pid = project.id;

  const jobs = useQuery({
    queryKey: ["jobs", pid, "active"],
    queryFn: () => apiFetch<JobList>(`/jobs?project_id=${pid}&active=true`),
    // Poll quickly only while something is running; otherwise check occasionally.
    refetchInterval: (q) => ((q.state.data?.items.length ?? 0) > 0 ? 1500 : 10_000),
  });
  const active = useMemo(() => jobs.data?.items ?? [], [jobs.data]);
  const recentQ = useQuery({
    queryKey: ["jobs", pid, "recent"],
    queryFn: () => apiFetch<JobList>(`/jobs?project_id=${pid}&limit=15`),
    refetchInterval: active.length > 0 ? 2000 : 15_000,
  });
  const recent = useMemo(() => recentQ.data?.items ?? [], [recentQ.data]);
  const wf = useWorkflow(pid);
  const run = wf.data?.run ?? null;
  const overview = useWorkspaceOverview(pid).data;
  const flagged = overview?.stages.find((s) => s.key === overview.current);
  const attentionView = flagged?.tab ?? null;

  const cancelM = useMutation({
    mutationFn: (j: Job) => apiFetch<Job>(`/jobs/${j.id}/cancel`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["jobs", pid] }),
    onError: () => reportError("Could not cancel the job."),
  });

  // Finished jobs: refresh every panel of the project (so chat and panels agree) and tell the person.
  const previous = useRef<Job[] | null>(null);
  useEffect(() => {
    if (!recentQ.data) return;
    const items = recentQ.data.items;
    if (previous.current) {
      const done = newlyFinished(previous.current, items);
      if (done.length) {
        qc.invalidateQueries({ predicate: (q) => q.queryKey[1] === pid && q.queryKey[0] !== "jobs" });
        for (const j of done) {
          const state = jobState(j);
          const view = JOB_VIEW[j.type];
          notify({
            key: `job:${j.id}`,
            tone: state === "failed" ? "error" : state === "completed" ? "success" : "info",
            title: `${jobLabel(j.type)} ${JOB_STATE_LABELS[state].toLowerCase()}`,
            body: j.error_summary ?? undefined,
            action: view ? { label: `Open ${viewLabel(view)}`, run: () => openView(view) } : undefined,
          });
        }
      }
    }
    previous.current = items;
  }, [recentQ.data, qc, pid, notify, openView]);

  // The protocol workflow asks its questions in the chat. Say so whenever a new one is waiting, and when the
  // workflow stops with a problem. (The first observation is the page loading, not news: stay quiet.)
  const lastQuestion = useRef<string | null | undefined>(undefined);
  const lastStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!wf.data) return;
    const q = run?.status === "awaiting_input" ? (run.pending_question_id ?? null) : null;
    if (lastQuestion.current !== undefined && q && q !== lastQuestion.current) {
      notify({
        key: "workflow:question",
        tone: "action",
        title: "The protocol setup has a question for you",
        body: `Next: ${questionName(run)}.`,
        action: { label: "Answer in chat", run: () => openChat(run?.conversation_id) },
      });
    }
    const st = run?.status;
    if (
      lastStatus.current !== undefined &&
      st !== lastStatus.current &&
      (st === "failed" || st === "needs_attention")
    ) {
      notify({
        key: "workflow:problem",
        tone: "error",
        title: "The protocol workflow stopped",
        body: run?.error_summary ?? undefined,
        action: { label: "Open the protocol", run: () => openView("protocol") },
      });
    }
    lastQuestion.current = q;
    lastStatus.current = st;
  }, [wf.data, run, notify, openChat, openView]);

  const value = useMemo<ActivityApi>(
    () => ({
      project,
      active,
      recent,
      jobsError: jobs.isError,
      run,
      overview,
      attentionView,
      cancel: (j) => cancelM.mutate(j),
      openView,
      openChat,
      openSteps,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [project, active, recent, jobs.isError, run, overview, attentionView, openView, openChat, openSteps],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useActivity(): ActivityApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useActivity must be used inside <ActivityProvider>");
  return v;
}

/** What the person should know right now, in one short phrase (no percentages: those would make screen
 *  readers re-announce every poll). */
function headline(a: ActivityApi): { text: string; tone: "busy" | "waiting" | "problem" | "idle" } {
  const failed = a.run?.status === "failed" || a.run?.status === "needs_attention";
  if (failed || a.project.status === "needs_attention") return { text: "Needs attention", tone: "problem" };
  const job = a.active[0];
  if (job) return { text: `${jobLabel(job.type)} running`, tone: "busy" };
  if (isBusy(a.run)) return { text: "Preparing the next question", tone: "busy" };
  if (a.run?.status === "awaiting_input") return { text: "Your answer is needed", tone: "waiting" };
  return { text: "Nothing running", tone: "idle" };
}

/** Top-right indicator: the current main step and what is running now. Opens a panel with live jobs. */
export function CurrentActivity() {
  const a = useActivity();
  const pop = usePopover();
  const idx = currentStepIndex(a.project.status, a.attentionView);
  const step = idx >= 0 ? REVIEW_STEPS[idx] : null;
  const h = headline(a);
  const job = a.active[0];
  const stepName = step?.label ?? stageLabel(a.project.status);
  const failedRecent = a.recent.filter((j) => jobState(j) === "failed").length;

  return (
    <div className="relative">
      <button
        ref={pop.triggerRef}
        type="button"
        data-testid="current-activity"
        aria-expanded={pop.open}
        aria-controls={pop.open ? pop.id : undefined}
        className={`flex h-8 max-w-[min(28rem,52vw)] items-center gap-2 rounded-md border px-2.5 text-xs hover:bg-raised ${
          h.tone === "problem" ? "border-danger/60" : h.tone === "waiting" ? "border-mark/60" : "border-line"
        }`}
        onClick={() => (pop.open ? pop.close() : pop.setOpen(true))}
      >
        {h.tone === "busy" ? (
          <Spinner className="text-mark" />
        ) : h.tone === "problem" ? (
          <Icon name="alert" size={14} className="text-danger" />
        ) : h.tone === "waiting" ? (
          <span aria-hidden className="h-2 w-2 rounded-full bg-mark" />
        ) : (
          <span aria-hidden className="h-2 w-2 rounded-full bg-ok" />
        )}
        <span className="truncate">
          <span className="font-semibold text-ink">{stepName}</span>
          {step && (
            <span className="hidden text-muted md:inline">
              {" "}
              · step {idx + 1} of {REVIEW_STEPS.length}
            </span>
          )}
          <span className="hidden text-muted sm:inline">
            {" "}
            · {h.text}
            {job && job.status === "running" ? ` ${job.progress}%` : ""}
          </span>
        </span>
        <Icon name="chevronDown" size={14} className="text-muted" />
      </button>
      {/* Announce changes of state, not progress ticks. */}
      <span className="sr-only" aria-live="polite">
        {`${stepName}. ${h.text}.`}
      </span>

      <PopoverPanel pop={pop} label="Review activity" className="w-[min(26rem,92vw)]">
        <div className="border-b border-line px-3 py-2.5">
          <p className="text-2xs text-muted">Current step</p>
          <p className="text-sm font-semibold">
            {step ? `${idx + 1}. ${step.label}` : stageLabel(a.project.status)}
          </p>
          {step && <p className="text-xs text-muted">{step.summary}</p>}
          <button
            type="button"
            className="mt-2 text-xs font-medium text-link hover:underline"
            onClick={() => {
              pop.close(false);
              a.openSteps();
            }}
          >
            Show all steps
          </button>
        </div>

        {a.run?.status === "awaiting_input" && (
          <div className="flex items-start gap-2 border-b border-line bg-mark/10 px-3 py-2.5">
            <span aria-hidden className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-mark" />
            <div className="min-w-0 flex-1">
              <p className="text-sm">Waiting for you: {questionName(a.run)}</p>
              <button
                type="button"
                className="mt-1 text-xs font-medium text-link hover:underline"
                onClick={() => {
                  pop.close(false);
                  a.openChat(a.run?.conversation_id);
                }}
              >
                Answer in chat
              </button>
            </div>
          </div>
        )}

        <section aria-labelledby="running-h" className="px-3 py-2.5">
          <h2 id="running-h" className="section-title mb-1.5">
            Running now
          </h2>
          {a.jobsError && <p className="text-xs text-danger">Job status is unavailable right now.</p>}
          {!a.jobsError && a.active.length === 0 && (
            <p className="text-xs text-muted">No background work is running.</p>
          )}
          <ul className="flex flex-col gap-2 text-xs">
            {a.active.map((j) => {
              const view = JOB_VIEW[j.type];
              return (
                <li key={j.id} className="flex flex-col gap-1" data-testid="active-job">
                  <ProgressIndicator label={jobLabel(j.type)} job={j} />
                  <span className="flex gap-3">
                    {view && (
                      <button
                        type="button"
                        className="text-link hover:underline"
                        onClick={() => {
                          pop.close(false);
                          a.openView(view);
                        }}
                      >
                        Open {viewLabel(view)}
                        <span className="sr-only"> ({stepOfView(view)?.label} step)</span>
                      </button>
                    )}
                    {!j.cancel_requested && !NOT_CANCELLABLE.has(j.type) && (
                      <button type="button" className="text-link hover:underline" onClick={() => a.cancel(j)}>
                        Cancel
                      </button>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>

        <details className="border-t border-line px-3 py-2.5 text-xs">
          <summary className="cursor-pointer text-muted">
            Recent jobs{failedRecent ? ` (${failedRecent} failed)` : ""}
          </summary>
          <p className="my-1.5 text-muted">
            A failed job keeps the work done before the failure; start it again from its step.
          </p>
          <ul className="flex max-h-60 flex-col gap-2 overflow-y-auto">
            {a.recent.map((j) => (
              <li key={j.id} className="border-b border-line pb-1 last:border-b-0">
                <ProgressIndicator label={jobLabel(j.type)} job={j} />
                <span className="block text-2xs text-muted">
                  Started {new Date(j.created_at).toLocaleString()}
                  {j.finished_at && ` · finished ${new Date(j.finished_at).toLocaleTimeString()}`}
                </span>
              </li>
            ))}
            {a.recent.length === 0 && <li className="text-muted">No jobs yet.</li>}
          </ul>
        </details>
      </PopoverPanel>
    </div>
  );
}
