"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import { WORKFLOW_STAGES } from "@/lib/protocol";
import {
  EXPRESSION_SOURCE_LABELS,
  QUERY_STATUS_LABELS,
  RUN_STATUS_LABELS,
  canRetry,
  fmtDate,
  incompleteWarning,
  isActiveRun,
} from "@/lib/search";
import type {
  ProviderInfo,
  SearchPlan,
  SearchRun,
  SearchRunList,
  SearchSummary,
  SearchTier,
} from "@/lib/types";
import { useStatus } from "@/components/status/StatusContext";
import { useProtocol, useWorkflow } from "@/components/protocol/hooks";

function errMessage(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

export function useSearchRuns(projectId: string) {
  return useQuery({
    queryKey: ["search-runs", projectId],
    queryFn: () => apiFetch<SearchRunList>(`/projects/${projectId}/search-runs`),
    refetchInterval: (q) => (q.state.data?.items.some(isActiveRun) ? 1500 : false),
  });
}

export function useSearchSummary(projectId: string, poll: boolean) {
  return useQuery({
    queryKey: ["search-summary", projectId],
    queryFn: () => apiFetch<SearchSummary>(`/projects/${projectId}/search/summary`),
    refetchInterval: poll ? 3000 : false,
  });
}

function useRefreshSearch(projectId: string) {
  const qc = useQueryClient();
  return () => {
    for (const k of ["search-runs", "search-summary", "papers", "jobs", "project", "workflow"])
      qc.invalidateQueries({ queryKey: [k, projectId] });
  };
}

/* ---------- plan editor (one per tier) ---------- */

function PlanEditor({
  projectId,
  tier,
  canWrite,
  disabled,
}: {
  projectId: string;
  tier: SearchTier;
  canWrite: boolean;
  disabled: boolean;
}) {
  const { reportError } = useStatus();
  const refresh = useRefreshSearch(projectId);
  const [expression, setExpression] = useState<string | null>(null); // null = use the protocol-derived query
  const [providers, setProviders] = useState<string[] | null>(null); // null = protocol defaults
  const [plan, setPlan] = useState<SearchPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);

  const preview = useMutation({
    mutationFn: (body: { expression: string | null; providers: string[] | null }) =>
      apiFetch<SearchPlan>(`/projects/${projectId}/search/plan`, {
        method: "POST",
        ...json({ tier, expression: body.expression, providers: body.providers }),
      }),
    onSuccess: (p) => {
      setPlan(p);
      setPlanError(null);
      if (providers === null) setProviders(p.providers);
    },
    onError: (e) => setPlanError(errMessage(e, "Could not build the search plan.")),
  });

  // First load: the plan the approved protocol produces.
  useEffect(() => {
    preview.mutate({ expression: null, providers: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, tier]);

  const start = useMutation({
    mutationFn: () =>
      apiFetch<SearchRun>(`/projects/${projectId}/search-runs`, {
        method: "POST",
        ...json({ tier, expression, providers }),
      }),
    onSuccess: refresh,
    onError: (e) => reportError(errMessage(e, "Could not start the search.")),
  });

  const catalog: ProviderInfo[] = (plan?.catalog ?? []).filter(
    (p) => tier === "additional" || p.tier === "open_access",
  );
  const shownExpression = expression ?? plan?.expression ?? "";
  const listed = new Set(plan?.protocol_sources.matched ?? []);

  return (
    <div className="flex flex-col gap-4">
      <fieldset>
        <legend className="section-title mb-2">Sources</legend>
        <ul className="flex flex-col gap-2">
          {catalog.map((p) => {
            const checked = (providers ?? plan?.providers ?? []).includes(p.key);
            return (
              <li key={p.key}>
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1 accent-mark"
                    checked={checked}
                    disabled={!canWrite || disabled}
                    onChange={(e) => {
                      const base = providers ?? plan?.providers ?? [];
                      const next = e.target.checked ? [...base, p.key] : base.filter((k) => k !== p.key);
                      setProviders(next);
                      preview.mutate({ expression, providers: next });
                    }}
                  />
                  <span>
                    <span className="text-ink">{p.label}</span>
                    {p.tier === "additional" && (
                      <span className="ml-2 text-xs text-muted">additional source</span>
                    )}
                    {!listed.has(p.key) && (
                      <span className="ml-2 text-xs text-mark">
                        not listed in the approved protocol (recorded as an addition)
                      </span>
                    )}
                    <span className="block text-xs text-muted">{p.description}</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      <div>
        <label htmlFor={`expr-${tier}`} className="section-title mb-1 block">
          Search expression
        </label>
        <textarea
          id={`expr-${tier}`}
          className="input min-h-[5rem] font-mono text-xs"
          value={shownExpression}
          disabled={!canWrite || disabled}
          onChange={(e) => setExpression(e.target.value)}
          aria-describedby={`expr-help-${tier}`}
        />
        <p id={`expr-help-${tier}`} className="mt-1 text-xs text-muted">
          Use AND, OR, NOT in capitals, &quot;quoted phrases&quot;, parentheses and a trailing * for
          truncation. It is translated into each source&apos;s own syntax below.
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-secondary"
            disabled={preview.isPending}
            onClick={() => preview.mutate({ expression, providers })}
          >
            Preview queries
          </button>
          {expression !== null && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setExpression(null);
                preview.mutate({ expression: null, providers });
              }}
            >
              Reset to the protocol-derived query
            </button>
          )}
        </div>
        {planError && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {planError}
          </p>
        )}
      </div>

      {plan && (
        <div className="flex flex-col gap-3 text-sm">
          <p className="text-muted">
            {EXPRESSION_SOURCE_LABELS[plan.expression_source] ?? plan.expression_source} · protocol version{" "}
            {plan.protocol_version}
          </p>
          {plan.derivation_notes.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-muted">
              {plan.derivation_notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
          {plan.queries.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <caption className="sr-only">Exact query sent to each source</caption>
                <thead className="text-muted">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Source</th>
                    <th className="py-1 pr-3 font-medium">Exact query</th>
                    <th className="py-1 font-medium">Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.queries.map((q) => (
                    <tr key={q.provider} className="border-t border-line align-top">
                      <td className="py-2 pr-3">{q.provider_label}</td>
                      <td className="py-2 pr-3 font-mono break-all">
                        {q.query_text}
                        {Object.keys(q.filters_applied).length > 0 && (
                          <span className="mt-1 block text-muted">
                            Filters: {JSON.stringify(q.filters_applied)}
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-muted">
                        {q.limitations.length ? q.limitations.join(" ") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {plan.limitations.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-muted">
              {plan.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted">
            Up to {plan.max_records_per_provider} records per source. A source reporting more is marked
            &quot;stopped at record limit&quot;, never complete.
          </p>
          <div>
            <button
              type="button"
              className="btn"
              disabled={!canWrite || disabled || !plan.ready || start.isPending}
              onClick={() => start.mutate()}
            >
              {tier === "additional" ? "Run the additional search" : "Run the search"}
            </button>
            {disabled && <span className="ml-3 text-xs text-muted">A search is already running.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- one run ---------- */

function RunCard({ projectId, run, canWrite }: { projectId: string; run: SearchRun; canWrite: boolean }) {
  const { reportError } = useStatus();
  const refresh = useRefreshSearch(projectId);
  const [open, setOpen] = useState(isActiveRun(run));
  const retry = useMutation({
    mutationFn: () =>
      apiFetch<SearchRun>(`/projects/${projectId}/search-runs/${run.id}/retry`, { method: "POST" }),
    onSuccess: refresh,
    onError: (e) => reportError(errMessage(e, "Could not continue the search.")),
  });
  const cancel = useMutation({
    mutationFn: () =>
      apiFetch<SearchRun>(`/projects/${projectId}/search-runs/${run.id}/cancel`, { method: "POST" }),
    onSettled: refresh,
    onError: (e) => reportError(errMessage(e, "Could not cancel the search.")),
  });
  const warning = incompleteWarning(run);
  return (
    <li className="rounded border border-line bg-panel px-4 py-3" data-testid="search-run">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="text-ink">{run.tier_label}</span>
          <span className="ml-2 text-xs text-muted">{fmtDate(run.created_at)}</span>
          {!run.is_current_protocol && (
            <span className="ml-2 text-xs text-mark">built from an earlier protocol version</span>
          )}
        </div>
        <span
          className={`text-sm ${
            run.status === "completed" ? "text-ok" : run.status === "failed" ? "text-danger" : "text-ink"
          }`}
          data-testid="search-run-status"
        >
          {RUN_STATUS_LABELS[run.status]}
        </span>
      </div>
      <p className="mt-1 text-sm text-muted">
        {run.records_retrieved} records retrieved · {run.papers_new} new papers ·{" "}
        {run.papers_matched_existing} already known · {run.duplicates_in_run} duplicates within this search
        {run.possible_duplicates > 0 && ` · ${run.possible_duplicates} possible duplicates to check`}
      </p>
      {warning && <p className="mt-1 text-sm text-mark">{warning}</p>}
      {run.error_summary && <p className="mt-1 text-sm text-danger">{run.error_summary}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" className="btn-ghost" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Show queries and provenance"}
        </button>
        {isActiveRun(run) && (
          <button
            type="button"
            className="btn-secondary"
            disabled={!canWrite || cancel.isPending}
            onClick={() => cancel.mutate()}
          >
            Cancel search
          </button>
        )}
        {canRetry(run) && (
          <button
            type="button"
            className="btn-secondary"
            disabled={!canWrite || retry.isPending}
            onClick={() => retry.mutate()}
          >
            Continue unfinished sources
          </button>
        )}
      </div>
      {open && (
        <div className="mt-3 flex flex-col gap-3 text-xs">
          <p>
            <span className="text-muted">Expression: </span>
            <span className="font-mono">{run.expression}</span>
            <span className="ml-2 text-muted">({EXPRESSION_SOURCE_LABELS[run.expression_source]})</span>
          </p>
          {run.derived_expression && run.derived_expression !== run.expression && (
            <p>
              <span className="text-muted">Protocol-derived expression (not used): </span>
              <span className="font-mono">{run.derived_expression}</span>
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <caption className="sr-only">Per-source search log</caption>
              <thead className="text-muted">
                <tr>
                  <th className="py-1 pr-3 font-medium">Source</th>
                  <th className="py-1 pr-3 font-medium">Exact query</th>
                  <th className="py-1 pr-3 font-medium">Reported</th>
                  <th className="py-1 pr-3 font-medium">Retrieved</th>
                  <th className="py-1 pr-3 font-medium">Status</th>
                  <th className="py-1 font-medium">Last retrieved</th>
                </tr>
              </thead>
              <tbody>
                {run.queries.map((q) => (
                  <tr key={q.id} className="border-t border-line align-top">
                    <td className="py-2 pr-3">
                      {q.provider_label}
                      {q.attribution && <span className="block text-2xs text-muted">{q.attribution}</span>}
                    </td>
                    <td className="py-2 pr-3 font-mono break-all">
                      {q.query_text}
                      <a
                        className="mt-1 block break-all"
                        href={q.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        Request URL
                      </a>
                      {q.limitations.length > 0 && (
                        <span className="mt-1 block font-sans text-muted">{q.limitations.join(" ")}</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">{q.total_reported ?? "—"}</td>
                    <td className="py-2 pr-3">
                      {q.records_retrieved}
                      {q.records_skipped > 0 && (
                        <span className="block text-muted">{q.records_skipped} unusable records skipped</span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {QUERY_STATUS_LABELS[q.status]}
                      {q.error_summary && <span className="block text-danger">{q.error_summary}</span>}
                    </td>
                    <td className="py-2">{fmtDate(q.retrieved_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {run.limitations.length > 0 && (
            <ul className="list-disc pl-5 text-muted">
              {run.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

/* ---------- broaden discovery ---------- */

function BroadenCard({
  projectId,
  summary,
  canWrite,
}: {
  projectId: string;
  summary: SearchSummary;
  canWrite: boolean;
}) {
  const { reportError } = useStatus();
  const refresh = useRefreshSearch(projectId);
  const prompt = summary.broaden_prompt;
  const decide = useMutation({
    mutationFn: (decision: "yes" | "no" | "later") =>
      apiFetch<SearchRun>(`/projects/${projectId}/search-runs/${prompt!.search_run_id}/broaden`, {
        method: "POST",
        ...json({ decision }),
      }),
    onSuccess: refresh,
    onError: (e) => reportError(errMessage(e, "Could not save your answer.")),
  });
  if (!prompt) return null;
  const answered = prompt.decision === "yes" || prompt.decision === "no";
  return (
    <section aria-labelledby="broaden-h" className="rounded border border-mark/50 px-4 py-3">
      <h3 id="broaden-h" className="mb-1 text-sm font-semibold">
        Broaden discovery to additional sources?
      </h3>
      <p className="max-w-prose text-sm text-muted">
        Additional sources such as Crossref list many papers whose full text is behind a subscription. You
        will see their metadata only; to review one, obtain a copy you are authorised to use. This application
        never bypasses paywalls.
      </p>
      {answered ? (
        <p className="mt-2 text-sm">
          Your answer: <span className="text-ink">{prompt.decision === "yes" ? "Yes" : "No"}</span>.{" "}
          <button
            type="button"
            className="btn-ghost"
            disabled={!canWrite}
            onClick={() => decide.mutate(prompt.decision === "yes" ? "no" : "yes")}
          >
            Change to {prompt.decision === "yes" ? "no" : "yes"}
          </button>
        </p>
      ) : (
        <div className="mt-2 flex gap-2">
          {(["yes", "no", "later"] as const).map((d) => (
            <button
              key={d}
              type="button"
              className={d === "yes" ? "btn" : "btn-secondary"}
              disabled={!canWrite || decide.isPending}
              onClick={() => decide.mutate(d)}
            >
              {d === "yes" ? "Yes" : d === "no" ? "No" : "Later"}
            </button>
          ))}
          {prompt.decision === "later" && (
            <span className="self-center text-xs text-muted">Asked again here.</span>
          )}
        </div>
      )}
    </section>
  );
}

/* ---------- counts ---------- */

function Counts({ summary }: { summary: SearchSummary }) {
  const c = summary.current_protocol ?? summary.all_runs;
  return (
    <section aria-labelledby="counts-h">
      <h3 id="counts-h" className="section-title mb-2">
        Identification{summary.current_protocol ? " (searches of the current protocol)" : ""}
      </h3>
      <dl className="grid grid-cols-[14rem_1fr] gap-y-1 text-sm" data-testid="search-counts">
        <dt className="text-muted">Records identified</dt>
        <dd>
          {c.records_identified}
          {Object.keys(c.records_by_provider).length > 0 && (
            <span className="ml-2 text-muted">
              (
              {Object.entries(c.records_by_provider)
                .map(([p, n]) => `${p} ${n}`)
                .join(", ")}
              )
            </span>
          )}
        </dd>
        <dt className="text-muted">Duplicates removed</dt>
        <dd>{c.duplicates_removed}</dd>
        <dt className="text-muted">Unique papers</dt>
        <dd data-testid="unique-papers">{c.unique_papers}</dd>
        <dt className="text-muted">Possible duplicates to check</dt>
        <dd>{c.possible_duplicates}</dd>
        <dt className="text-muted">Open PDF link reported</dt>
        <dd>{c.access.open_pdf_link ?? 0}</dd>
        <dt className="text-muted">Open landing page only</dt>
        <dd>{c.access.open_landing_page ?? 0}</dd>
        <dt className="text-muted">Metadata only</dt>
        <dd>{c.access.metadata_only ?? 0}</dd>
      </dl>
      <p className="mt-1 max-w-prose text-xs text-muted">{summary.counting_note}</p>
      {summary.incomplete_runs > 0 && (
        <p className="mt-1 text-xs text-mark">
          {summary.incomplete_runs} search{summary.incomplete_runs === 1 ? " is" : "es are"} incomplete; these
          counts do not describe a complete search.
        </p>
      )}
    </section>
  );
}

/* ---------- panel ---------- */

export default function SearchPanel({
  projectId,
  canWrite,
  onOpenProtocol,
  onOpenPapers,
  onOpenScreening,
}: {
  projectId: string;
  canWrite: boolean;
  onOpenProtocol: () => void;
  onOpenPapers: () => void;
  onOpenScreening?: () => void;
}) {
  const wf = useWorkflow(projectId);
  const protocol = useProtocol(projectId);
  const runs = useSearchRuns(projectId);
  const active = runs.data?.items.find(isActiveRun) ?? null;
  const summary = useSearchSummary(projectId, active !== null);

  if (!wf.data || !protocol.data) return <p className="text-muted">Loading…</p>;
  const run = wf.data.run;
  const atSearch =
    run !== null &&
    WORKFLOW_STAGES.indexOf(run.stage as (typeof WORKFLOW_STAGES)[number]) >=
      WORKFLOW_STAGES.indexOf("SEARCH");
  const ready = protocol.data.approved !== null && atSearch && run?.status === "idle" && !protocol.data.draft;

  const history = runs.data?.items ?? [];
  const broadenYes = summary.data?.broaden_prompt?.decision === "yes";

  return (
    <div className="flex max-w-4xl flex-col gap-8">
      {!ready && (
        <section className="max-w-prose">
          <h2 className="mb-2 text-base font-semibold">Search is not available yet</h2>
          <p className="text-muted">
            {protocol.data.approved === null
              ? "Searches are built from the approved review protocol. Approve the protocol first."
              : "A protocol revision or workflow step is in progress. Finish it, then search from the approved version."}
          </p>
          <button type="button" className="btn-secondary mt-3" onClick={onOpenProtocol}>
            Open the protocol
          </button>
        </section>
      )}

      {ready && (
        <section aria-labelledby="plan-h">
          <h2 id="plan-h" className="section-title mb-3">
            Search plan: open-access-first sources
          </h2>
          <p className="mb-4 max-w-prose text-sm text-muted">
            These sources report open-access status and lawful links to open copies. Results are real records
            from the providers; nothing is generated.
          </p>
          <PlanEditor
            projectId={projectId}
            tier="open_access"
            canWrite={canWrite}
            disabled={active !== null}
          />
        </section>
      )}

      {summary.data && (
        <>
          {ready && <BroadenCard projectId={projectId} summary={summary.data} canWrite={canWrite} />}
          {ready && broadenYes && (
            <section aria-labelledby="plan2-h">
              <h2 id="plan2-h" className="section-title mb-3">
                Search plan: additional sources
              </h2>
              <PlanEditor
                projectId={projectId}
                tier="additional"
                canWrite={canWrite}
                disabled={active !== null}
              />
            </section>
          )}
          {summary.data.run_count > 0 && (
            <div className="flex flex-col gap-2">
              <Counts summary={summary.data} />
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-secondary" onClick={onOpenPapers}>
                  Open the papers
                </button>
                {onOpenScreening && (
                  <button type="button" className="btn" onClick={onOpenScreening}>
                    Start screening
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}

      <section aria-labelledby="history-h">
        <h2 id="history-h" className="section-title mb-3">
          Search history
        </h2>
        {runs.isError && <p className="text-danger">Could not load the search history.</p>}
        {runs.data && history.length === 0 && <p className="text-muted">No searches have been run yet.</p>}
        <ul className="flex flex-col gap-3">
          {history.map((r) => (
            <RunCard key={r.id} projectId={projectId} run={r} canWrite={canWrite} />
          ))}
        </ul>
      </section>
    </div>
  );
}
