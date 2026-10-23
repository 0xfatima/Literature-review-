"use client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import {
  BANDS,
  BAND_DOTS,
  BAND_LABELS,
  BAND_STYLES,
  DECISION_LABELS,
  FIELD_LABELS,
  JUDGEMENT_LABELS,
  SUGGESTION_LABELS,
  progressPercent,
} from "@/lib/screening";
import { fmtDate } from "@/lib/search";
import type {
  Assessment,
  Band,
  HumanDecision,
  Job,
  QueueItem,
  RelevanceStatus,
  ScreeningDecisionRecord,
  ScreeningQueue,
  ScreeningReason,
} from "@/lib/types";
import { useStatus } from "@/components/status/StatusContext";

const PAGE = 20;
const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function useRefresh(projectId: string) {
  const qc = useQueryClient();
  return () => {
    for (const k of ["screening", "relevance", "jobs", "project", "workflow"])
      qc.invalidateQueries({ queryKey: [k, projectId] });
  };
}

/* ---------- relevance band ---------- */

function BandChip({ band, score, source }: { band: Band; score: number | null; source: string | null }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs ${BAND_STYLES[band]}`}
      data-testid="relevance-band"
    >
      <span aria-hidden>{BAND_DOTS[band]}</span>
      <span>{BAND_LABELS[band]}</span>
      {score !== null && band !== "not_assessed" && <span className="text-muted">· {score}/100</span>}
      {source && <span className="text-muted">· {source === "ai" ? "AI" : "word overlap"}</span>}
    </span>
  );
}

function LexicalWhy({ a }: { a: Assessment }) {
  return (
    <div className="text-xs">
      <p className="text-muted">{a.explanation.summary}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {(a.explanation.concepts ?? []).map((c) => (
          <li key={c.id}>
            <span className="text-ink">{c.label}</span>
            <span className="text-muted"> ({c.score}/100)</span>
            {c.matched.length > 0 ? (
              <span>
                {" "}
                · matched “{c.matched.join("”, “")}” in the{" "}
                {c.best_field ? FIELD_LABELS[c.best_field] : "record"}
              </span>
            ) : (
              <span className="text-muted"> · no shared words</span>
            )}
            {c.missing.length > 0 && c.matched.length > 0 && (
              <span className="text-muted"> · not found: {c.missing.join(", ")}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function AiWhy({ a }: { a: Assessment }) {
  const d = a.suggestion_detail;
  return (
    <div className="flex flex-col gap-2 text-xs">
      <p>
        <span className="text-muted">AI relevance reason: </span>
        {a.explanation.summary || "—"}
      </p>
      {(a.explanation.questions ?? []).length > 0 && (
        <ul className="flex flex-col gap-0.5">
          {a.explanation.questions!.map((q) => (
            <li key={q.question}>
              {q.question}: <span className="text-ink">{q.relevance}</span>
              <span className="text-muted"> — {q.text}</span>
            </li>
          ))}
        </ul>
      )}
      {a.eligibility_suggestion && (
        <div className="rounded border border-dashed border-line px-3 py-2" data-testid="ai-suggestion">
          <p>
            <span className="font-semibold text-ink">{SUGGESTION_LABELS[a.eligibility_suggestion]}</span>
            <span className="text-muted"> — a suggestion only; it is not recorded as a decision.</span>
          </p>
          {d?.reason && <p className="mt-1 text-muted">{d.reason}</p>}
          {(d?.criteria ?? []).length > 0 && (
            <ul className="mt-1 flex flex-col gap-1">
              {d!.criteria.map((c) => (
                <li key={c.criterion}>
                  {c.kind} criterion “{c.text}”:{" "}
                  <span className="text-ink">{JUDGEMENT_LABELS[c.judgement]}</span>
                  {c.evidence_quote && (
                    <span className="block text-muted">
                      Quote (verified in the record): “{c.evidence_quote}”
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {a.warnings.length > 0 && (
        <ul className="list-disc pl-5 text-mark">
          {a.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ---------- decision controls ---------- */

function ExcludeForm({
  reasons,
  requireReason,
  onSubmit,
  onCancel,
  busy,
}: {
  reasons: ScreeningReason[];
  requireReason: boolean;
  onSubmit: (reason: string | null, note: string | null) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const needsNote = reason === "other" && !note.trim();
  const blocked = (requireReason && !reason) || needsNote;
  return (
    <div className="mt-2 flex flex-col gap-2 rounded border border-line p-3">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Exclusion reason {requireReason ? "(required)" : "(recommended)"}
        <select className="input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus>
          <option value="">{requireReason ? "Choose a reason" : "No reason given"}</option>
          {reasons.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Note {reason === "other" ? "(required for “other”)" : "(optional)"}
        <textarea
          className="input min-h-[3rem]"
          value={note}
          maxLength={2000}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <button
          type="button"
          className="btn"
          disabled={busy || blocked}
          onClick={() => onSubmit(reason || null, note.trim() || null)}
        >
          Exclude
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function History({
  projectId,
  paperId,
  onUndo,
}: {
  projectId: string;
  paperId: string;
  onUndo: (id: string) => void;
}) {
  const h = useQuery({
    queryKey: ["screening", projectId, "history", paperId],
    queryFn: () =>
      apiFetch<{ decisions: ScreeningDecisionRecord[] }>(
        `/projects/${projectId}/screening/papers/${paperId}/history`,
      ),
  });
  if (!h.data) return <p className="text-xs text-muted">Loading history…</p>;
  const list = h.data.decisions;
  if (list.length === 0) return <p className="text-xs text-muted">No decisions recorded yet.</p>;
  const last = list[list.length - 1]!;
  return (
    <div className="text-xs">
      <ol className="flex flex-col gap-1">
        {list.map((d) => (
          <li key={d.id}>
            <span className="text-ink">{d.decision_label}</span>
            <span className="text-muted">
              {" "}
              · {fmtDate(d.created_at)} ·{" "}
              {d.action === "undo" ? "undo" : d.action === "bulk" ? "bulk action" : "decision"}
            </span>
            {d.reason_label && <span className="block text-muted">Reason: {d.reason_label}</span>}
            {d.note && <span className="block text-muted">Note: {d.note}</span>}
          </li>
        ))}
      </ol>
      {last.decision !== "cleared" && (
        <button type="button" className="btn-ghost mt-1" onClick={() => onUndo(last.id)}>
          Undo the latest decision
        </button>
      )}
    </div>
  );
}

function Card({
  projectId,
  item,
  reasons,
  requireReason,
  selected,
  onSelect,
  canWrite,
  onUndone,
}: {
  projectId: string;
  item: QueueItem;
  reasons: ScreeningReason[];
  requireReason: boolean;
  selected: boolean;
  onSelect: (v: boolean) => void;
  canWrite: boolean;
  onUndone: () => void;
}) {
  const { reportError } = useStatus();
  const refresh = useRefresh(projectId);
  const [excluding, setExcluding] = useState(false);
  const [why, setWhy] = useState(false);
  const [history, setHistory] = useState(false);
  const [long, setLong] = useState(false);
  const decide = useMutation({
    mutationFn: (b: { decision: HumanDecision; reason_code?: string | null; note?: string | null }) =>
      apiFetch(`/projects/${projectId}/screening/papers/${item.paper_id}/decision`, {
        method: "POST",
        ...json({ ...b, expected_row_version: item.state_row_version ?? 0 }),
      }),
    onSuccess: () => setExcluding(false),
    onSettled: refresh,
    onError: (e) => reportError(errText(e, "Could not save the decision.")),
  });
  const undo = useMutation({
    mutationFn: (decisionId: string) =>
      apiFetch(`/projects/${projectId}/screening/undo`, {
        method: "POST",
        ...json({ decision_id: decisionId }),
      }),
    onSuccess: onUndone,
    onSettled: refresh,
    onError: (e) => reportError(errText(e, "Could not undo.")),
  });
  const shown = item.ai ?? item.lexical;
  const abstract = item.abstract ?? "";
  const busy = decide.isPending || !canWrite;
  const titleId = `t-${item.paper_id}`;
  return (
    <li
      className="rounded border border-line bg-panel px-4 py-3"
      data-testid="screening-card"
      aria-labelledby={titleId}
      onKeyDown={(e) => {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
        if (e.target instanceof HTMLSelectElement || busy || excluding) return;
        const k = e.key.toLowerCase();
        if (k === "i") decide.mutate({ decision: "include" });
        else if (k === "u") decide.mutate({ decision: "uncertain" });
        else if (k === "e") setExcluding(true);
        else return;
        e.preventDefault();
      }}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1.5 accent-mark"
          checked={selected}
          onChange={(e) => onSelect(e.target.checked)}
          aria-label={`Select “${item.title}”`}
        />
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="text-ink">
            {item.title}
          </h3>
          <p className="text-xs text-muted">
            {item.authors.slice(0, 6).join(", ")}
            {item.author_count > 6 && ` and ${item.author_count - 6} more`}
            {item.year && ` · ${item.year}`}
            {item.venue && ` · ${item.venue}`}
            {item.publication_type && ` · ${item.publication_type}`}
            {` · found in ${item.providers.join(", ")}`}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <BandChip band={item.band} score={shown ? shown.score : null} source={item.band_source} />
            {item.ai?.eligibility_suggestion && (
              <span className="rounded border border-dashed border-line px-2 py-0.5 text-xs">
                {SUGGESTION_LABELS[item.ai.eligibility_suggestion]}
              </span>
            )}
            {item.is_preprint && <span className="text-xs text-mark">Preprint</span>}
            {item.is_retracted && <span className="text-xs text-danger">Retracted</span>}
            {item.possible_duplicate_of_id && <span className="text-xs text-mark">Possible duplicate</span>}
            {shown && (
              <button
                type="button"
                className="btn-ghost text-xs"
                aria-expanded={why}
                onClick={() => setWhy(!why)}
              >
                {why ? "Hide explanation" : "Why this score?"}
              </button>
            )}
          </div>
          {why && (
            <div className="mt-2 flex flex-col gap-3 rounded bg-canvas px-3 py-2">
              {item.lexical && <LexicalWhy a={item.lexical} />}
              {item.ai && <AiWhy a={item.ai} />}
              <p className="text-2xs text-muted">
                Relevance is not eligibility: a relevant record can still fail an inclusion criterion.
              </p>
            </div>
          )}
          <p className="mt-2 max-w-prose whitespace-pre-wrap text-sm">
            {abstract ? (
              long || abstract.length <= 700 ? (
                abstract
              ) : (
                <>
                  {abstract.slice(0, 700)}…{" "}
                  <button type="button" className="btn-ghost text-xs" onClick={() => setLong(true)}>
                    Show the full abstract
                  </button>
                </>
              )
            ) : (
              <span className="text-muted">
                No abstract available. Judge from the title, or mark as uncertain.
              </span>
            )}
          </p>

          <div
            className="mt-3 flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Screening decision"
          >
            <span className="text-sm" data-testid="decision-label">
              Decision: <span className="text-ink">{item.decision_label}</span>
              {item.decision && item.decision_is_current_protocol === false && (
                <span className="ml-1 text-xs text-mark">
                  (made under an earlier protocol version; review it)
                </span>
              )}
            </span>
            {(["include", "uncertain"] as const).map((d) => (
              <button
                key={d}
                type="button"
                className={item.decision === d ? "btn" : "btn-secondary"}
                aria-pressed={item.decision === d}
                aria-keyshortcuts={d === "include" ? "I" : "U"}
                disabled={busy}
                onClick={() => decide.mutate({ decision: d })}
              >
                {DECISION_LABELS[d]}
              </button>
            ))}
            <button
              type="button"
              className={item.decision === "exclude" ? "btn" : "btn-secondary"}
              aria-pressed={item.decision === "exclude"}
              aria-keyshortcuts="E"
              disabled={busy}
              onClick={() => setExcluding(true)}
            >
              {DECISION_LABELS.exclude}…
            </button>
            <button
              type="button"
              className="btn-ghost text-xs"
              aria-expanded={history}
              onClick={() => setHistory(!history)}
            >
              {history ? "Hide history" : "History"}
            </button>
          </div>
          {excluding && (
            <ExcludeForm
              reasons={reasons}
              requireReason={requireReason}
              busy={decide.isPending}
              onCancel={() => setExcluding(false)}
              onSubmit={(reason_code, note) => decide.mutate({ decision: "exclude", reason_code, note })}
            />
          )}
          {history && (
            <div className="mt-2">
              <History projectId={projectId} paperId={item.paper_id} onUndo={(id) => undo.mutate(id)} />
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/* ---------- relevance controls ---------- */

function RelevanceControls({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const { reportError } = useStatus();
  const qc = useQueryClient();
  const [ack, setAck] = useState(false);
  const status = useQuery({
    queryKey: ["relevance", projectId],
    queryFn: () => apiFetch<RelevanceStatus>(`/projects/${projectId}/screening/relevance`),
    refetchInterval: (q) => (q.state.data?.active_job_id ? 2000 : false),
  });
  const wasActive = status.data?.active_job_id;
  const start = useMutation({
    mutationFn: (b: { use_llm: boolean; force?: boolean }) =>
      apiFetch<Job>(`/projects/${projectId}/screening/relevance`, { method: "POST", ...json(b) }),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["relevance", projectId] });
      qc.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
    onError: (e) => reportError(errText(e, "Could not start the relevance assessment.")),
  });
  if (!status.data) return null;
  const s = status.data;
  const count = (m: Partial<Record<Band, number>>) => Object.values(m).reduce((a, b) => a + (b ?? 0), 0);
  const lexN = count(s.counts.lexical);
  const aiN = count(s.counts.llm);
  const msg = s.last_result?.message as string | null | undefined;
  return (
    <section aria-labelledby="rel-h" className="rounded border border-line px-4 py-3">
      <h2 id="rel-h" className="section-title mb-2">
        Relevance assessment
      </h2>
      <p className="max-w-prose text-sm text-muted">
        Relevance orders the queue; it never decides. Word overlap ({s.methods.lexical}) compares the records
        with your approved questions and search concepts: {lexN} of {s.total_papers} records scored.
        {s.llm.configured
          ? ` AI assessment (${s.llm.model}, ${s.methods.llm}): ${aiN} of ${s.total_papers} records assessed.`
          : " No AI model is configured on this server."}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-secondary"
          disabled={!canWrite || Boolean(wasActive) || start.isPending}
          onClick={() => start.mutate({ use_llm: false })}
        >
          {lexN < s.total_papers ? "Score word overlap" : "Rescore new records"}
        </button>
        {s.llm.configured && (
          <button
            type="button"
            className="btn"
            disabled={!canWrite || Boolean(wasActive) || start.isPending || !ack}
            onClick={() => start.mutate({ use_llm: true })}
          >
            {aiN > 0 ? "Continue AI assessment" : "Run AI assessment"}
          </button>
        )}
        {wasActive && (
          <span className="text-sm text-muted">Assessing… progress is shown in the status bar.</span>
        )}
      </div>
      {s.llm.configured && (
        <label className="mt-2 flex max-w-prose items-start gap-2 text-xs text-muted">
          <input
            type="checkbox"
            className="mt-0.5 accent-mark"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
          />
          <span>
            I understand what is sent: {s.llm.data_sent} {s.llm.provider_notice}
          </span>
        </label>
      )}
      {msg && <p className="mt-2 text-sm text-mark">{msg}</p>}
      {s.last_error && <p className="mt-2 text-sm text-danger">{s.last_error}</p>}
      {s.model_runs.length > 0 && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-muted">Recent AI calls ({s.model_runs.length})</summary>
          <ul className="mt-1 flex flex-col gap-0.5">
            {s.model_runs.map((r) => (
              <li key={r.id}>
                {fmtDate(r.created_at)} · {r.model} · {r.prompt_version} · {r.item_count} records ·{" "}
                <span className={r.status === "failed" ? "text-danger" : "text-ink"}>{r.status}</span>
                {r.input_tokens !== null && ` · ${r.input_tokens}+${r.output_tokens ?? 0} tokens`}
                {r.duration_ms !== null && ` · ${(r.duration_ms / 1000).toFixed(1)} s`}
                {r.error_summary && <span className="block text-muted">{r.error_summary}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/* ---------- panel ---------- */

export default function ScreeningPanel({
  projectId,
  canWrite,
  onOpenSearch,
}: {
  projectId: string;
  canWrite: boolean;
  onOpenSearch: () => void;
}) {
  const { reportError } = useStatus();
  const refresh = useRefresh(projectId);
  const [decision, setDecision] = useState("undecided"); // resume where you left off
  const [band, setBand] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [text, setText] = useState("");
  const [applied, setApplied] = useState("");
  const [sort, setSort] = useState("relevance");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkExclude, setBulkExclude] = useState(false);
  const [lastBatch, setLastBatch] = useState<{ id: string; count: number; label: string } | null>(null);

  const params = new URLSearchParams({ sort, offset: String(offset), limit: String(PAGE) });
  if (decision) params.set("decision", decision);
  if (band) params.set("band", band);
  if (suggestion) params.set("suggestion", suggestion);
  if (applied) params.set("q", applied);
  const queue = useQuery({
    queryKey: ["screening", projectId, "queue", params.toString()],
    queryFn: () => apiFetch<ScreeningQueue>(`/projects/${projectId}/screening/queue?${params.toString()}`),
    placeholderData: keepPreviousData,
    retry: (n, e) => !(e instanceof ApiError && e.status === 409) && n < 2,
  });
  const reasons = useQuery({
    queryKey: ["screening", projectId, "reasons"],
    queryFn: () =>
      apiFetch<{ items: ScreeningReason[]; require_reason: boolean }>(
        `/projects/${projectId}/screening/reasons`,
      ),
    enabled: queue.isSuccess,
  });
  const bulk = useMutation({
    mutationFn: (b: { decision: HumanDecision; reason_code?: string | null; note?: string | null }) =>
      apiFetch<{ batch_id: string | null; changed: number }>(`/projects/${projectId}/screening/bulk`, {
        method: "POST",
        ...json({ ...b, paper_ids: [...selected] }),
      }),
    onSuccess: (r, b) => {
      setSelected(new Set());
      setBulkExclude(false);
      setLastBatch(
        r.batch_id ? { id: r.batch_id, count: r.changed, label: DECISION_LABELS[b.decision] } : null,
      );
    },
    onSettled: refresh,
    onError: (e) => reportError(errText(e, "Could not apply the bulk decision.")),
  });
  const undoBatch = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ undone: number; skipped_changed_since: number }>(`/projects/${projectId}/screening/undo`, {
        method: "POST",
        ...json({ batch_id: id }),
      }),
    onSuccess: (r) => {
      setLastBatch(null);
      if (r.skipped_changed_since)
        reportError(
          `${r.skipped_changed_since} record(s) were changed after the bulk action and were left as they are.`,
        );
    },
    onSettled: refresh,
    onError: (e) => reportError(errText(e, "Could not undo.")),
  });

  if (queue.isError) {
    const e = queue.error;
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">Screening is not available yet</h2>
        <p className="text-muted">
          {e instanceof ApiError && e.status === 409 ? e.message : "Could not load the screening queue."}
        </p>
        <button type="button" className="btn-secondary mt-3" onClick={onOpenSearch}>
          Go to search
        </button>
      </div>
    );
  }
  if (!queue.data) return <p className="text-muted">Loading the screening queue…</p>;
  const q = queue.data;
  const p = q.progress;
  if (p.total === 0)
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">No records to screen yet</h2>
        <p className="text-muted">Run a search first; every record it finds appears here for screening.</p>
        <button type="button" className="btn-secondary mt-3" onClick={onOpenSearch}>
          Go to search
        </button>
      </div>
    );
  const done = p.include + p.exclude + p.uncertain;
  const reset = (fn: () => void) => {
    fn();
    setOffset(0);
    setSelected(new Set());
  };
  const allOnPage = q.items.length > 0 && q.items.every((i) => selected.has(i.paper_id));

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <section aria-labelledby="prog-h">
        <h2 id="prog-h" className="section-title mb-1">
          Title and abstract screening · protocol version {q.protocol_version}
        </h2>
        <p className="text-sm" data-testid="screening-progress">
          {done} of {p.total} records decided ({progressPercent(done, p.total)}%): {p.include} include,{" "}
          {p.exclude} exclude, {p.uncertain} uncertain, {p.undecided} still to screen.
        </p>
        <progress
          className="mt-1 h-1.5 w-full max-w-md accent-mark"
          max={p.total}
          value={done}
          aria-label="Screening progress"
        />
        <p className="mt-1 text-xs text-muted">
          Decisions are saved as you make them; you can stop at any time and continue from “Still to screen”.
          Keyboard: focus any control in a record, then press I (include), E (exclude) or U (uncertain).
        </p>
      </section>

      <RelevanceControls projectId={projectId} canWrite={canWrite} />

      <form
        role="search"
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          reset(() => setApplied(text.trim()));
        }}
      >
        <label className="flex flex-col text-xs text-muted">
          Show
          <select
            className="input w-40"
            value={decision}
            onChange={(e) => reset(() => setDecision(e.target.value))}
          >
            <option value="undecided">Still to screen</option>
            <option value="include">Included</option>
            <option value="exclude">Excluded</option>
            <option value="uncertain">Uncertain</option>
            <option value="">All records</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Relevance
          <select className="input w-52" value={band} onChange={(e) => reset(() => setBand(e.target.value))}>
            <option value="">Any relevance</option>
            {BANDS.map((b) => (
              <option key={b} value={b}>
                {BAND_LABELS[b]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          AI suggestion
          <select
            className="input w-44"
            value={suggestion}
            onChange={(e) => reset(() => setSuggestion(e.target.value))}
          >
            <option value="">Any</option>
            <option value="include">Suggests include</option>
            <option value="exclude">Suggests exclude</option>
            <option value="uncertain">Suggests uncertain</option>
            <option value="none">No AI suggestion</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Sort
          <select className="input w-48" value={sort} onChange={(e) => reset(() => setSort(e.target.value))}>
            <option value="relevance">Most relevant first</option>
            <option value="relevance_asc">Least relevant first</option>
            <option value="year">Newest year first</option>
            <option value="title">Title</option>
            <option value="recent_decision">Recently decided</option>
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Title or abstract contains
          <input
            className="input w-56"
            value={text}
            maxLength={200}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <button type="submit" className="btn-secondary">
          Filter
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-2 text-sm" aria-live="polite">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            className="accent-mark"
            checked={allOnPage}
            onChange={(e) => {
              const next = new Set(selected);
              for (const i of q.items) {
                if (e.target.checked) next.add(i.paper_id);
                else next.delete(i.paper_id);
              }
              setSelected(next);
            }}
          />
          Select this page
        </label>
        <span className="text-muted">
          {q.total} record{q.total === 1 ? "" : "s"} shown · {selected.size} selected
        </span>
        {selected.size > 0 && (
          <span className="flex flex-wrap gap-2" role="group" aria-label="Bulk decision">
            <button
              type="button"
              className="btn-secondary"
              disabled={!canWrite || bulk.isPending}
              onClick={() => bulk.mutate({ decision: "include" })}
            >
              Include selected
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={!canWrite || bulk.isPending}
              onClick={() => setBulkExclude(true)}
            >
              Exclude selected…
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={!canWrite || bulk.isPending}
              onClick={() => bulk.mutate({ decision: "uncertain" })}
            >
              Mark selected uncertain
            </button>
          </span>
        )}
        {lastBatch && (
          <span className="flex items-center gap-2">
            <span>
              {lastBatch.label}: {lastBatch.count} record{lastBatch.count === 1 ? "" : "s"}.
            </span>
            <button
              type="button"
              className="btn-ghost"
              disabled={undoBatch.isPending}
              onClick={() => undoBatch.mutate(lastBatch.id)}
            >
              Undo
            </button>
          </span>
        )}
      </div>
      {bulkExclude && reasons.data && (
        <ExcludeForm
          reasons={reasons.data.items}
          requireReason={reasons.data.require_reason}
          busy={bulk.isPending}
          onCancel={() => setBulkExclude(false)}
          onSubmit={(reason_code, note) => bulk.mutate({ decision: "exclude", reason_code, note })}
        />
      )}

      {q.items.length === 0 ? (
        <p className="text-muted">
          {decision === "undecided"
            ? "Every record matching these filters has a decision."
            : "No records match these filters."}
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {q.items.map((item) => (
            <Card
              key={item.paper_id}
              projectId={projectId}
              item={item}
              reasons={reasons.data?.items ?? []}
              requireReason={reasons.data?.require_reason ?? false}
              selected={selected.has(item.paper_id)}
              onSelect={(v) => {
                const next = new Set(selected);
                if (v) next.add(item.paper_id);
                else next.delete(item.paper_id);
                setSelected(next);
              }}
              canWrite={canWrite}
              onUndone={() => setLastBatch(null)}
            />
          ))}
        </ul>
      )}

      {q.total > PAGE && (
        <nav aria-label="Pages" className="flex items-center gap-3 text-sm">
          <button
            type="button"
            className="btn-secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            Previous
          </button>
          <span className="text-muted">
            {offset + 1}–{Math.min(offset + PAGE, q.total)} of {q.total}
          </span>
          <button
            type="button"
            className="btn-secondary"
            disabled={offset + PAGE >= q.total}
            onClick={() => setOffset(offset + PAGE)}
          >
            Next
          </button>
        </nav>
      )}
    </div>
  );
}
