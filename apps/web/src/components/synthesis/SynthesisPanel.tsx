"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useStatus } from "@/components/status/StatusContext";
import { ApiError, apiFetch, json } from "@/lib/api";
import { formatValue } from "@/lib/extraction";
import {
  CERTAINTY_LABELS,
  CERTAINTY_TONE,
  KIND_LABELS,
  ORIGIN_LABELS,
  PATTERN_LABELS,
  acceptBlocker,
  gapSummary,
  humanize,
} from "@/lib/synthesis";
import type {
  AiJobStatus,
  Certainty,
  Comparison,
  EvidenceHistory,
  StatementKind,
  SynthesisStatement,
  SynthesisVersion,
} from "@/lib/types";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const KINDS = Object.keys(KIND_LABELS) as StatementKind[];
const CERTAINTIES = Object.keys(CERTAINTY_LABELS) as Certainty[];

function SourceCitation({
  projectId,
  schemaId,
  paperId,
  fieldKey,
  label,
}: {
  projectId: string;
  schemaId: string;
  paperId: string;
  fieldKey: string;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["evidence", projectId, "history", schemaId, paperId, fieldKey],
    queryFn: () =>
      apiFetch<EvidenceHistory>(
        `/projects/${projectId}/extraction/schemas/${schemaId}/evidence/${paperId}/${fieldKey}/history`,
      ),
    enabled: open,
  });
  return (
    <li>
      <button type="button" className="text-link" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        {label}
      </button>
      {open && data?.item && (
        <div className="mt-1 rounded border border-line bg-canvas p-2">
          <div>
            {data.item.status === "found" ? formatValue(data.item.value) : humanize(data.item.status)}
            {data.item.page_no && ` · p. ${data.item.page_no}`}
          </div>
          {data.item.excerpt && <div className="text-muted">“{data.item.excerpt}”</div>}
          {data.source_page && (
            <div className="text-muted">
              {data.source_page.filename}, page {data.source_page.page_no}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function StatementCard({
  s,
  projectId,
  version,
  cmp,
  canWrite,
  onReview,
}: {
  s: SynthesisStatement;
  projectId: string;
  version: SynthesisVersion;
  cmp: Comparison | undefined;
  canWrite: boolean;
  onReview: (body: Record<string, unknown>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(s.text);
  const titles = Object.fromEntries((cmp?.studies ?? []).map((x) => [x.paper_id, x.title]));
  const labels = Object.fromEntries((cmp?.schema?.fields ?? []).map((f) => [f.field_key, f.label]));
  const draft = version.status === "draft" && canWrite;
  return (
    <li className="rounded border border-line bg-panel p-3 text-sm">
      <div className="mb-1 flex flex-wrap gap-2 text-2xs">
        <span className="font-semibold">{KIND_LABELS[s.kind]}</span>
        <span className={CERTAINTY_TONE[s.certainty]}>{CERTAINTY_LABELS[s.certainty]}</span>
        <span className="text-muted">{ORIGIN_LABELS[s.origin]}</span>
        <span className="text-muted">Review: {s.review}</span>
        {s.group && <span className="text-muted">Group: {s.group}</span>}
        {s.stale && <span className="text-danger">Out of date: evidence changed</span>}
      </div>
      {editing ? (
        <textarea
          className="input"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Statement text"
        />
      ) : (
        <p>{s.text}</p>
      )}
      <details className="mt-1 text-xs">
        <summary className="cursor-pointer text-muted">
          Why “{CERTAINTY_LABELS[s.certainty]}” · {s.citations.length} source(s)
        </summary>
        <ul className="list-disc pl-5 text-muted">
          {s.certainty_reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
          {s.warnings.map((w, i) => (
            <li key={`w${i}`} className="text-danger">
              {w.message}
            </li>
          ))}
          {s.stale_reasons.map((r, i) => (
            <li key={`s${i}`} className="text-danger">
              {r}
            </li>
          ))}
        </ul>
        <ul className="mt-1 flex flex-col gap-1">
          {s.citations.map((c) => (
            <SourceCitation
              key={c.evidence_item_id}
              projectId={projectId}
              schemaId={version.extraction_schema_id}
              paperId={c.paper_id}
              fieldKey={c.field_key}
              label={`${titles[c.paper_id] ?? "Study"} — ${labels[c.field_key] ?? c.field_key}`}
            />
          ))}
        </ul>
        {s.appraisals.length > 0 && (
          <p className="mt-1 text-muted">
            Appraisals:{" "}
            {s.appraisals.map((a) => `${titles[a.paper_id] ?? "study"}: ${humanize(a.overall)}`).join("; ")}
          </p>
        )}
      </details>
      {draft && (
        <div className="mt-2 flex flex-wrap gap-2">
          {editing ? (
            <>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  onReview({ action: "edit", statement_id: s.id, text });
                  setEditing(false);
                }}
              >
                Save edit
              </button>
              <button type="button" className="btn-ghost" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="btn-accept-soft"
                onClick={() => onReview({ action: "accept", statement_id: s.id })}
              >
                Accept
              </button>
              <button type="button" className="btn-ghost" onClick={() => setEditing(true)}>
                Edit
              </button>
              <button
                type="button"
                className="btn-reject-soft"
                onClick={() => onReview({ action: "reject", statement_id: s.id })}
              >
                Reject
              </button>
              {s.stale && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => onReview({ action: "refresh", statement_id: s.id })}
                >
                  Refresh citations
                </button>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

function AddStatement({ cmp, onAdd }: { cmp: Comparison; onAdd: (body: Record<string, unknown>) => void }) {
  const [kind, setKind] = useState<StatementKind>("conclusion");
  const [text, setText] = useState("");
  const [claimed, setClaimed] = useState<Certainty>("uncertain");
  const [cites, setCites] = useState<string[]>([]);
  const options = cmp.studies.flatMap((st) =>
    Object.entries(st.cells)
      .filter(([, c]) => c.reviewed)
      .map(([k, c]) => ({
        id: c.item_id,
        label: `${st.title.slice(0, 60)} — ${cmp.schema?.fields.find((f) => f.field_key === k)?.label ?? k}: ${
          c.status === "found" ? formatValue(c.value).slice(0, 40) : humanize(c.status)
        }`,
      })),
  );
  return (
    <details className="rounded border border-line p-3 text-sm">
      <summary className="cursor-pointer">Add a statement</summary>
      <div className="mt-2 flex flex-col gap-2">
        <label className="text-2xs text-muted" htmlFor="add-kind">
          Kind
        </label>
        <select
          id="add-kind"
          className="input"
          value={kind}
          onChange={(e) => setKind(e.target.value as StatementKind)}
        >
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k]}
            </option>
          ))}
        </select>
        <label className="text-2xs text-muted" htmlFor="add-text">
          Statement
        </label>
        <textarea
          id="add-text"
          className="input"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <label className="text-2xs text-muted" htmlFor="add-cites">
          Cited evidence (reviewed values only)
        </label>
        <select
          id="add-cites"
          className="input"
          multiple
          size={Math.min(8, Math.max(3, options.length))}
          value={cites}
          onChange={(e) => setCites(Array.from(e.target.selectedOptions).map((o) => o.value))}
        >
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <label className="text-2xs text-muted" htmlFor="add-cert">
          Your certainty (the rules may lower it, never raise it)
        </label>
        <select
          id="add-cert"
          className="input"
          value={claimed}
          onChange={(e) => setClaimed(e.target.value as Certainty)}
        >
          {CERTAINTIES.map((c) => (
            <option key={c} value={c}>
              {CERTAINTY_LABELS[c]}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn self-start"
          disabled={!text.trim() || cites.length === 0}
          onClick={() => {
            onAdd({ action: "add", kind, text, citations: cites, claimed_certainty: claimed });
            setText("");
            setCites([]);
          }}
        >
          Add statement
        </button>
      </div>
    </details>
  );
}

export default function SynthesisPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const [groupBy, setGroupBy] = useState("");
  const [useAi, setUseAi] = useState(false);
  const [note, setNote] = useState("");
  const { data: cmp } = useQuery({
    queryKey: ["synthesis", projectId, "comparison", groupBy],
    queryFn: () =>
      apiFetch<Comparison>(
        `/projects/${projectId}/synthesis/comparison${groupBy ? `?group_by=${encodeURIComponent(groupBy)}` : ""}`,
      ),
  });
  const { data: versions } = useQuery({
    queryKey: ["synthesis", projectId, "versions"],
    queryFn: () => apiFetch<SynthesisVersion[]>(`/projects/${projectId}/synthesis/versions`),
  });
  const { data: job } = useQuery({
    queryKey: ["synthesis", projectId, "drafts"],
    queryFn: () => apiFetch<AiJobStatus>(`/projects/${projectId}/synthesis/drafts`),
    refetchInterval: (q) => (q.state.data?.active_job_id ? 3000 : false),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["synthesis", projectId] });
  const onErr = (f: string) => (e: unknown) => reportError(errText(e, f));
  const draftJob = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/synthesis/drafts`, {
        method: "POST",
        ...json({ group_by: groupBy || null, use_ai: useAi }),
      }),
    onSuccess: refresh,
    onError: onErr("Could not start the synthesis draft."),
  });
  const current =
    versions?.find((v) => v.status === "draft") ?? versions?.find((v) => v.status === "accepted");
  const review = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch(`/projects/${projectId}/synthesis/versions/${current?.id}/review`, {
        method: "POST",
        ...json({ ...body, expected_row_version: current?.row_version }),
      }),
    onSuccess: refresh,
    onError: onErr("Could not update the statement."),
  });
  const accept = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/synthesis/versions/${current?.id}/accept`, {
        method: "POST",
        ...json({ note, expected_row_version: current?.row_version }),
      }),
    onSuccess: refresh,
    onError: onErr("Could not accept the synthesis."),
  });
  const revise = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/synthesis/versions/${current?.id}/revise`, { method: "POST" }),
    onSuccess: refresh,
    onError: onErr("Could not start a revision."),
  });

  if (!cmp) return <p className="text-muted">Loading…</p>;
  if (!cmp.schema)
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">Nothing to synthesise yet</h2>
        <p className="text-muted">
          Approve an extraction schema and review extracted values first. Comparisons and the synthesis use
          reviewed values only.
        </p>
      </div>
    );
  const blocker = current?.status === "draft" ? acceptBlocker(current.statements) : null;
  const titles = Object.fromEntries(cmp.studies.map((s) => [s.paper_id, s.title]));
  return (
    <div className="flex flex-col gap-6" data-testid="synthesis-panel">
      <section aria-label="Comparison tables" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end gap-2">
          <h2 className="mr-auto text-base font-semibold">Compare studies</h2>
          <label className="text-2xs text-muted" htmlFor="group-by">
            Group by
          </label>
          <select
            id="group-by"
            className="input !w-64"
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value)}
          >
            <option value="">No grouping</option>
            {cmp.group_options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <p className="text-xs text-muted">
          {cmp.meta_analysis.note}
          {cmp.unreviewed_values
            ? ` ${cmp.unreviewed_values} extracted value(s) are not reviewed yet and are excluded.`
            : ""}
        </p>
        {cmp.groups.map((g) => (
          <div key={g.key} className="overflow-x-auto rounded border border-line">
            <table className="min-w-full text-left text-xs">
              <caption className="bg-raised p-2 text-left font-semibold">
                {g.label} · {g.paper_ids.length} stud{g.paper_ids.length === 1 ? "y" : "ies"}
              </caption>
              <thead>
                <tr className="border-t border-line">
                  <th className="p-2">Field</th>
                  <th className="p-2">Pattern</th>
                  <th className="p-2">Values (studies)</th>
                  <th className="p-2">Gaps</th>
                </tr>
              </thead>
              <tbody>
                {g.fields.map((f) => (
                  <tr key={f.field_key} className="border-t border-line align-top">
                    <th scope="row" className="p-2 font-normal">
                      {f.label}
                    </th>
                    <td className="p-2">{PATTERN_LABELS[f.pattern]}</td>
                    <td className="p-2">
                      {f.values.map((v) => (
                        <div key={v.value}>
                          {v.value}{" "}
                          <span className="text-muted">
                            ({v.paper_ids.map((p) => titles[p]?.slice(0, 30)).join("; ")})
                          </span>
                        </div>
                      ))}
                      {f.range && (
                        <div className="text-muted">
                          Range {f.range.min}–{f.range.max} across {f.range.n} studies (not pooled)
                        </div>
                      )}
                    </td>
                    <td className="p-2 text-muted">{gapSummary(f) || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {cmp.limitations && cmp.limitations.serious_appraisal_concerns.length > 0 && (
          <p className="text-xs text-danger">
            Serious appraisal concerns:{" "}
            {cmp.limitations.serious_appraisal_concerns.map((p) => titles[p]).join("; ")}
          </p>
        )}
        {cmp.limitations && cmp.limitations.not_appraised.length > 0 && (
          <p className="text-xs text-muted">
            Not yet appraised: {cmp.limitations.not_appraised.length} stud(ies).
          </p>
        )}
      </section>

      <section aria-label="Synthesis" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-auto text-base font-semibold">
            Narrative synthesis
            {current && (
              <span className="ml-2 text-xs font-normal text-muted">
                v{current.version} · {current.status === "draft" ? "draft — needs your review" : "accepted"}
              </span>
            )}
          </h2>
          {canWrite && !versions?.some((v) => v.status === "draft") && (
            <>
              <label className="flex items-center gap-1 text-xs">
                <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
                Include AI-drafted statements
              </label>
              <button
                type="button"
                className="btn"
                onClick={() => draftJob.mutate()}
                disabled={!!job?.active_job_id || draftJob.isPending}
              >
                Generate draft
              </button>
              {current?.status === "accepted" && (
                <button type="button" className="btn-secondary" onClick={() => revise.mutate()}>
                  Revise accepted version
                </button>
              )}
            </>
          )}
        </div>
        {useAi && job && !job.consent.can_send_documents && (
          <p className="text-xs text-muted">
            AI drafting is off: {job.consent.blocked_reason} Rule-based statements are still drafted.
          </p>
        )}
        {job?.active_job_id && (
          <p className="text-xs text-muted" role="status">
            Drafting in the background…
          </p>
        )}
        {job?.last_error && <p className="text-xs text-danger">{job.last_error}</p>}
        {current ? (
          <>
            <ul className="flex flex-col gap-2">
              {current.statements.map((s) => (
                <StatementCard
                  key={s.id}
                  s={s}
                  projectId={projectId}
                  version={current}
                  cmp={cmp}
                  canWrite={canWrite}
                  onReview={(b) => review.mutate(b)}
                />
              ))}
            </ul>
            {current.status === "draft" && canWrite && (
              <>
                <AddStatement cmp={cmp} onAdd={(b) => review.mutate(b)} />
                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-1 flex-col text-2xs text-muted">
                    Acceptance note
                    <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
                  </label>
                  <button
                    type="button"
                    className="btn-accept"
                    onClick={() => accept.mutate()}
                    disabled={!!blocker || accept.isPending}
                    title={blocker ?? "Accept this synthesis version"}
                  >
                    Accept synthesis
                  </button>
                </div>
                {blocker && <p className="text-xs text-muted">{blocker}</p>}
              </>
            )}
            {current.status === "accepted" && current.accept_note && (
              <p className="text-xs text-muted">Acceptance note: {current.accept_note}</p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted">
            No synthesis yet. Generate a draft: rule-based statements describe agreements, disagreements, gaps
            and limitations from reviewed evidence; you review each one before accepting.
          </p>
        )}
      </section>
    </div>
  );
}
