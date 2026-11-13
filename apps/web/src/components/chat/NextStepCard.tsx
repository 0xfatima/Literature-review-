"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import Icon from "@/components/ui/Icon";
import FinishScreening from "@/components/screening/FinishScreening";
import { friendlyAiError } from "@/lib/screening";
import { ApiError, apiFetch, json } from "@/lib/api";
import type {
  Job,
  ProtocolOverview,
  RelevanceStatus,
  ScreeningQueue,
  SearchPlan,
  SearchRun,
  SearchRunList,
} from "@/lib/types";

/** After the protocol is approved the guided chat keeps going, one plain question at a time:
 *  1. "Here is the search I'll run. OK?"  (run it / edit it myself / ask the AI for a different one)
 *  2. while searching: progress
 *  3. "Found N papers. Let the AI screen them, or screen them yourself?"
 *  4. while the AI screens: progress; afterwards what it decided, with a link to review.
 *  Everything here calls the same endpoints as the step panels, so the panels always agree with the chat. */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function Spinner() {
  return (
    <span
      aria-hidden
      className="spin inline-block h-3.5 w-3.5 shrink-0 rounded-full border-2 border-mark border-t-transparent"
    />
  );
}

function Bubble({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <li className="mr-4 rounded-lg border border-mark/50 bg-canvas px-3 py-2" aria-label={label}>
      <div className="mb-1 text-2xs font-semibold text-ink">Assistant</div>
      {children}
    </li>
  );
}

const isActive = (r: SearchRun) => r.status === "queued" || r.status === "running";

export default function NextStepCard({
  projectId,
  canWrite,
  protocol,
  openTab,
}: {
  projectId: string;
  canWrite: boolean;
  protocol: ProtocolOverview;
  openTab: (view: string) => void;
}) {
  const approvedId = protocol.approved?.id ?? null;
  const ready = approvedId !== null && protocol.draft === null;

  const runs = useQuery({
    queryKey: ["search-runs", projectId],
    queryFn: () => apiFetch<SearchRunList>(`/projects/${projectId}/search-runs`),
    enabled: ready,
    refetchInterval: (q) => (q.state.data?.items.some(isActive) ? 2000 : false),
  });
  const current = (runs.data?.items ?? []).filter((r) => r.protocol_version_id === approvedId);
  const latest = current[0] ?? null;
  const searched = current.some((r) => !isActive(r) && r.status !== "cancelled");

  if (!ready || !runs.data) return null;
  if (latest && isActive(latest)) return <Searching run={latest} />;
  if (!searched) return <SearchProposal projectId={projectId} canWrite={canWrite} openTab={openTab} />;
  return <ScreeningStep projectId={projectId} canWrite={canWrite} openTab={openTab} run={latest} />;
}

/* ---------- 1. the search ---------- */

function SearchProposal({
  projectId,
  canWrite,
  openTab,
}: {
  projectId: string;
  canWrite: boolean;
  openTab: (view: string) => void;
}) {
  const qc = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [error, setError] = useState<string | null>(null);
  const planKey = ["search-plan", projectId];
  const plan = useQuery({
    queryKey: planKey,
    queryFn: () =>
      apiFetch<SearchPlan>(`/projects/${projectId}/search/plan`, {
        method: "POST",
        ...json({ tier: "open_access", expression: null, providers: null }),
      }),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const regenerate = useMutation({
    mutationFn: () =>
      apiFetch<SearchPlan>(`/projects/${projectId}/search/suggest-expression`, {
        method: "POST",
        ...json({
          tier: "open_access",
          expression: null,
          providers: null,
          guidance: guidance.trim() || null,
        }),
      }),
    onSuccess: (p) => {
      qc.setQueryData(planKey, p);
      setAsking(false);
      setGuidance("");
      setError(null);
    },
    onError: (e) => setError(errText(e, "The AI could not write a new search.")),
  });
  const run = useMutation({
    mutationFn: () =>
      apiFetch<SearchRun>(`/projects/${projectId}/search-runs`, {
        method: "POST",
        ...json({ tier: "open_access", expression: null, providers: null }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["search-runs", projectId] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["workspace", projectId] });
    },
    onError: (e) => setError(errText(e, "Could not start the search.")),
  });

  if (plan.isPending)
    return (
      <Bubble label="Search proposal">
        <p className="flex items-center gap-2" aria-live="polite">
          <Spinner /> Writing a search from your protocol…
        </p>
      </Bubble>
    );
  if (plan.isError || !plan.data)
    return (
      <Bubble label="Search proposal">
        <p>Your protocol is approved. The next step is the search.</p>
        <p className="mt-1 text-sm text-danger">{errText(plan.error, "The search could not be prepared.")}</p>
        <button type="button" className="btn-secondary mt-2" onClick={() => openTab("search")}>
          Open the Search step
        </button>
      </Bubble>
    );

  const p = plan.data;
  const byAi = p.expression_source === "ai";
  const busy = run.isPending || regenerate.isPending;
  return (
    <Bubble label="Search proposal">
      <p>
        Your protocol is approved. Here&apos;s the search I&apos;ll run in {p.providers.length} open-access
        databases. Is this okay?
      </p>
      {p.expression ? (
        <pre className="mt-2 whitespace-pre-wrap break-words rounded-md bg-raised px-3 py-2 font-mono text-xs text-ink">
          {p.expression}
        </pre>
      ) : (
        <p className="mt-2 text-sm text-danger">
          No search could be built from the protocol. Write one yourself.
        </p>
      )}
      <p className="mt-1 text-xs text-muted">
        {byAi
          ? "Written by the AI from your topic, questions and criteria."
          : "A rough keyword search (no AI model was available to write a full one). Check it before running."}
      </p>

      {asking ? (
        <div className="mt-3 flex flex-col gap-2">
          <label htmlFor="search-guidance" className="text-sm">
            What should the new search do differently? <span className="text-muted">(optional)</span>
          </label>
          <textarea
            id="search-guidance"
            className="input min-h-[3.5rem]"
            maxLength={800}
            placeholder="e.g. focus on hospital and clinical use; include deep learning on quantum hardware"
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            autoFocus
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn"
              disabled={!canWrite || busy}
              onClick={() => regenerate.mutate()}
            >
              {regenerate.isPending ? (
                <>
                  <Spinner /> Writing…
                </>
              ) : (
                "Write a new search"
              )}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setAsking(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-accept"
            disabled={!canWrite || busy || !p.ready}
            onClick={() => run.mutate()}
          >
            <Icon name="check" size={14} /> {run.isPending ? "Starting…" : "Yes, run this search"}
          </button>
          <button type="button" className="btn-secondary" onClick={() => openTab("search")}>
            Edit it myself
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={!canWrite || busy}
            onClick={() => setAsking(true)}
          >
            <Icon name="sparkle" size={14} /> Ask AI for a different one
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </Bubble>
  );
}

function Searching({ run }: { run: SearchRun }) {
  return (
    <Bubble label="Search in progress">
      <p className="flex items-center gap-2" aria-live="polite">
        <Spinner /> Searching {run.providers.length} databases… {run.records_retrieved} records so far.
      </p>
      <p className="mt-1 text-xs text-muted">
        This usually takes one to three minutes. You can keep working.
      </p>
    </Bubble>
  );
}

/* ---------- 2. screening ---------- */

type AiDecisions = { include: number; exclude: number; uncertain: number; kept_human: number };

/** "I'll screen them myself" is remembered per project in this browser, so the chat stops offering the AI. */
const manualKey = (projectId: string) => `srs:manual-screening:${projectId}`;
function readManual(projectId: string): boolean {
  try {
    return window.localStorage.getItem(manualKey(projectId)) === "1";
  } catch {
    return false;
  }
}
function writeManual(projectId: string, on: boolean) {
  try {
    if (on) window.localStorage.setItem(manualKey(projectId), "1");
    else window.localStorage.removeItem(manualKey(projectId));
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}

/** Quota and rate-limit stops, said plainly with what to do next. */
function quotaHelp(msg: string | null | undefined): string | null {
  if (!msg || !/rate limit|quota/i.test(msg)) return null;
  return "Your AI provider's free limit for now is used up, so the AI stopped. Records it already sorted keep their scores. You can screen the rest yourself, or let the AI continue later (free limits usually reset within a minute for per-minute limits, or the next day for daily ones).";
}

function ScreeningStep({
  projectId,
  canWrite,
  openTab,
  run,
}: {
  projectId: string;
  canWrite: boolean;
  openTab: (view: string) => void;
  run: SearchRun | null;
}) {
  const qc = useQueryClient();
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManualState] = useState(() => readManual(projectId));
  const setManual = (on: boolean) => {
    writeManual(projectId, on);
    setManualState(on);
  };
  const rel = useQuery({
    queryKey: ["relevance", projectId],
    queryFn: () => apiFetch<RelevanceStatus>(`/projects/${projectId}/screening/relevance`),
    refetchInterval: (q) => (q.state.data?.active_job_id ? 2000 : false),
  });
  const activeId = rel.data?.active_job_id ?? null;
  const job = useQuery({
    queryKey: ["jobs", projectId, activeId],
    queryFn: () => apiFetch<Job>(`/jobs/${activeId}`),
    enabled: activeId !== null,
    refetchInterval: 2000,
  });
  const queue = useQuery({
    queryKey: ["screening", projectId, "queue", "chat-progress", activeId],
    queryFn: () =>
      apiFetch<ScreeningQueue>(`/projects/${projectId}/screening/queue?limit=1&decision=undecided`),
    retry: false,
    refetchInterval: activeId ? 4000 : false,
  });
  const start = useMutation({
    mutationFn: () =>
      apiFetch<Job>(`/projects/${projectId}/screening/relevance`, {
        method: "POST",
        ...json({ use_llm: true, auto_decide: true }),
      }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["relevance", projectId] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => setError(errText(e, "Could not start AI screening.")),
  });

  const progress = queue.data?.progress;
  const total = progress?.total ?? 0;
  const found = run?.papers_new ?? total;
  const searchLine =
    run?.status === "failed" ? (
      <p className="text-sm text-danger">
        The search failed. {run.error_summary ?? ""}{" "}
        <button type="button" className="underline" onClick={() => openTab("search")}>
          Open the Search step
        </button>
      </p>
    ) : (
      <p>
        The search found <strong>{found}</strong> new paper{found === 1 ? "" : "s"}
        {total && total !== found ? ` (${total} in this review in total)` : ""}.{" "}
        <button type="button" className="text-link underline" onClick={() => openTab("papers")}>
          See them
        </button>
      </p>
    );

  if (!rel.data || !progress)
    return (
      <Bubble label="Screening">
        {searchLine}
        <p className="mt-1 flex items-center gap-2 text-sm text-muted">
          <Spinner /> Loading…
        </p>
      </Bubble>
    );

  if (activeId) {
    const j = job.data;
    return (
      <Bubble label="AI screening in progress">
        {searchLine}
        <p className="mt-2 flex items-center gap-2" aria-live="polite">
          <Spinner />
          {j?.cancel_requested
            ? "Stopping after the current papers…"
            : (j?.progress_message ?? "Starting the AI…")}
        </p>
        <progress
          className="mt-2 h-1.5 w-full accent-mark"
          max={100}
          value={j?.progress ?? 0}
          aria-label="AI screening progress"
        />
        <p className="mt-1 text-xs text-muted">
          Decisions appear in Screening as they are made.{" "}
          <button type="button" className="underline" onClick={() => openTab("screening")}>
            Watch in Screening
          </button>
        </p>
      </Bubble>
    );
  }

  const decided = rel.data.last_result?.ai_decisions as AiDecisions | undefined;
  const stopMsg = rel.data.last_result?.message as string | null | undefined;
  const done = progress.include + progress.exclude + progress.uncertain;
  const ai = rel.data.llm;

  return (
    <Bubble label="Screening">
      {searchLine}
      {decided && decided.include + decided.exclude + decided.uncertain > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <span>The AI decided:</span>
          <span className="badge-accept">{decided.include} include</span>
          <span className="badge-reject">{decided.exclude} exclude</span>
          <span className="badge-uncertain">{decided.uncertain} uncertain</span>
        </div>
      )}
      {!manual && stopMsg && <p className="mt-1 text-sm text-mark">{quotaHelp(stopMsg) ?? stopMsg}</p>}
      {!manual && rel.data.last_error && (
        <p className="mt-1 text-sm text-danger">{friendlyAiError(rel.data.last_error)}</p>
      )}

      {progress.undecided === 0 ? (
        <>
          <p className="mt-2">
            Every record has a decision: {progress.include} included, {progress.exclude} excluded,{" "}
            {progress.uncertain} uncertain.
            {progress.uncertain > 0 &&
              ` Decide the ${progress.uncertain} uncertain record${progress.uncertain === 1 ? "" : "s"} to complete the step.`}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className="btn-accept" onClick={() => openTab("screening")}>
              Review the decisions
            </button>
            <button type="button" className="btn-secondary" onClick={() => openTab("fulltext")}>
              Next: full texts
            </button>
          </div>
        </>
      ) : manual ? (
        <>
          <p className="mt-2">
            You&apos;re screening yourself: <strong>{done}</strong> of {progress.total} decided (
            {progress.include} included). Include the papers you want in the Screening step.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={() => openTab("screening")}>
              Open Screening
            </button>
            {ai.configured && (
              <button type="button" className="btn-ghost text-xs" onClick={() => setManual(false)}>
                Let the AI help instead
              </button>
            )}
          </div>
          <div className="mt-3 border-t border-line pt-3">
            <FinishScreening projectId={projectId} progress={progress} canWrite={canWrite} />
          </div>
        </>
      ) : (
        <>
          <p className="mt-2">
            {done > 0
              ? `${progress.undecided} records still need a decision. How do you want to screen them?`
              : "How do you want to screen them?"}
          </p>
          {ai.configured ? (
            <>
              <label className="mt-2 flex items-start gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-mark"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                />
                <span>
                  OK to send titles, abstracts and my protocol to {ai.model}. The AI only decides records I
                  haven&apos;t decided, and I can change or undo every decision.
                </span>
              </label>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-accept"
                  disabled={!canWrite || !ack || start.isPending}
                  onClick={() => start.mutate()}
                >
                  <Icon name="sparkle" size={14} /> {start.isPending ? "Starting…" : "Let the AI screen them"}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => {
                    setManual(true);
                    openTab("screening");
                  }}
                >
                  I&apos;ll screen them myself
                </button>
              </div>
            </>
          ) : (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setManual(true);
                  openTab("screening");
                }}
              >
                Start screening
              </button>
              <span className="text-xs text-muted">
                Connect an AI model in Settings to let the AI screen for you.
              </span>
            </div>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
    </Bubble>
  );
}
