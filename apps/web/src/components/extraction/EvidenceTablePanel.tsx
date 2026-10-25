"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useStatus } from "@/components/status/StatusContext";
import { ApiError, apiFetch, json } from "@/lib/api";
import {
  EVIDENCE_LABELS,
  cellText,
  downloadEvidenceCsv,
  formatValue,
  highlight,
  parseInput,
} from "@/lib/extraction";
import type {
  AffectedOutputs,
  ConsentStatus,
  EvidenceHistory,
  EvidenceItem,
  EvidenceStatus,
  EvidenceTable,
  EvidenceTableRow,
  ExtractionRunStatus,
  ExtractionSchema,
  FieldDefinition,
} from "@/lib/types";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const EDIT_STATUSES: EvidenceStatus[] = [
  "found",
  "not_located",
  "ambiguous",
  "unavailable",
  "not_applicable",
  "pending",
];

function ConsentBox({
  projectId,
  consent,
  canWrite,
}: {
  projectId: string;
  consent: ConsentStatus;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const done = () => qc.invalidateQueries({ queryKey: ["evidence", projectId] });
  const grant = useMutation({
    mutationFn: () =>
      apiFetch(`/projects/${projectId}/llm-consent`, {
        method: "POST",
        ...json({ notice_version: consent.notice_version }),
      }),
    onSuccess: done,
    onError: (e) => reportError(errText(e, "Could not record consent.")),
  });
  const revoke = useMutation({
    mutationFn: () => apiFetch(`/projects/${projectId}/llm-consent`, { method: "DELETE" }),
    onSuccess: done,
    onError: (e) => reportError(errText(e, "Could not revoke consent.")),
  });
  const askable = consent.configured && consent.operator_allows_documents && consent.terms_reviewed;
  return (
    <section className="rounded border border-line bg-panel p-3 text-sm" aria-label="AI document consent">
      {consent.consent ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-ok">
            Consent given to send document text to {consent.provider} / {consent.model}.
          </span>
          {canWrite && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => revoke.mutate()}
              disabled={revoke.isPending}
            >
              Revoke consent
            </button>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-muted">
            AI extraction is off for this project:{" "}
            {consent.blocked_reason ?? "document text may not be sent."} Papers without full text can still be
            marked, and every field can be extracted manually.
          </p>
          {askable && canWrite && (
            <>
              <p className="rounded border border-line bg-canvas p-2 text-xs">
                {consent.notice} Provider: {consent.provider}; model: {consent.model}.
              </p>
              <button
                type="button"
                className="btn-secondary self-start"
                onClick={() => grant.mutate()}
                disabled={grant.isPending}
              >
                I have read this and consent
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function SourcePage({ history }: { history: EvidenceHistory }) {
  const page = history.source_page;
  if (!page) {
    const pages = history.item?.pages_inspected ?? [];
    return (
      <p className="text-xs text-muted">
        No page is cited.{pages.length > 0 && ` Pages inspected: ${pages.join(", ")}.`}
      </p>
    );
  }
  const parts = highlight(page.text, history.item?.excerpt ?? null);
  return (
    <div>
      <p className="mb-1 text-xs text-muted">
        {page.filename}, page {page.page_no}
        {!page.is_current_text && " (from an earlier text extraction of this file)"}
      </p>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded border border-line bg-canvas p-2 text-xs">
        {parts ? (
          <>
            {parts[0]}
            <mark className="bg-mark/30 text-ink">{parts[1]}</mark>
            {parts[2]}
          </>
        ) : (
          page.text
        )}
      </pre>
    </div>
  );
}

function EditForm({
  base,
  field,
  item,
  documentId,
  onSaved,
}: {
  base: string;
  field: FieldDefinition;
  item: EvidenceItem | null;
  documentId: string | null;
  onSaved: () => void;
}) {
  const { reportError } = useStatus();
  const [status, setStatus] = useState<EvidenceStatus>(
    item?.status === "pending" ? "found" : (item?.status ?? "found"),
  );
  const [value, setValue] = useState(formatValue(item?.value));
  const [excerpt, setExcerpt] = useState(item?.excerpt ?? "");
  const [pageNo, setPageNo] = useState(item?.page_no ? String(item.page_no) : "");
  const [note, setNote] = useState(item?.note ?? "");
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<EvidenceItem>(base, { method: "PUT", ...json(body) }),
    onSuccess: () => {
      setFormError(null);
      onSaved();
    },
    onError: (e) => {
      setFormError(errText(e, "Could not save."));
      reportError(errText(e, "Could not save the evidence value."));
    },
  });
  const submit = () => {
    const body: Record<string, unknown> = { status, note: note || null, reason: reason || null };
    if (item) body.expected_row_version = item.row_version;
    if (status === "found") {
      const parsed = parseInput(field, value);
      if ("error" in parsed) return setFormError(parsed.error);
      body.value = parsed.value;
    }
    if (excerpt.trim() && status !== "unavailable") {
      const n = Number(pageNo);
      if (!documentId || !Number.isInteger(n) || n < 1)
        return setFormError("Give the page number of the excerpt (a linked full text is required).");
      Object.assign(body, { excerpt, document_id: documentId, page_no: n });
    }
    save.mutate(body);
  };
  const id = `edit-${field.field_key}`;
  return (
    <div className="flex flex-col gap-2 rounded border border-line bg-canvas p-3">
      <label className="text-2xs text-muted" htmlFor={`${id}-status`}>
        Status
      </label>
      <select
        id={`${id}-status`}
        className="input"
        value={status}
        onChange={(e) => setStatus(e.target.value as EvidenceStatus)}
      >
        {EDIT_STATUSES.map((s) => (
          <option key={s} value={s}>
            {EVIDENCE_LABELS[s]}
          </option>
        ))}
      </select>
      {status === "found" && (
        <>
          <label className="text-2xs text-muted" htmlFor={`${id}-value`}>
            Value ({field.type}
            {field.options?.length ? `: ${field.options.join("; ")}` : ""})
          </label>
          <input
            id={`${id}-value`}
            className="input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </>
      )}
      <label className="text-2xs text-muted" htmlFor={`${id}-excerpt`}>
        Source excerpt (copied exactly from the page)
      </label>
      <textarea
        id={`${id}-excerpt`}
        className="input"
        rows={3}
        value={excerpt}
        onChange={(e) => setExcerpt(e.target.value)}
      />
      <label className="text-2xs text-muted" htmlFor={`${id}-page`}>
        Page number
      </label>
      <input
        id={`${id}-page`}
        className="input !w-28"
        inputMode="numeric"
        value={pageNo}
        onChange={(e) => setPageNo(e.target.value)}
      />
      <label className="text-2xs text-muted" htmlFor={`${id}-note`}>
        Note {status === "ambiguous" && "(required: explain the ambiguity)"}
      </label>
      <input id={`${id}-note`} className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      <label className="text-2xs text-muted" htmlFor={`${id}-reason`}>
        Reason for this change (kept in the history)
      </label>
      <input
        id={`${id}-reason`}
        className="input"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      {formError && (
        <p role="alert" className="text-xs text-danger">
          {formError}
        </p>
      )}
      <button type="button" className="btn self-start" onClick={submit} disabled={save.isPending}>
        Save correction
      </button>
    </div>
  );
}

function CellDetail({
  projectId,
  schemaId,
  row,
  field,
  canWrite,
  onClose,
}: {
  projectId: string;
  schemaId: string;
  row: EvidenceTableRow;
  field: FieldDefinition;
  canWrite: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const [editing, setEditing] = useState(false);
  const base = `/projects/${projectId}/extraction/schemas/${schemaId}/evidence/${row.paper.id}/${field.field_key}`;
  const { data: history } = useQuery({
    queryKey: ["evidence", projectId, "history", schemaId, row.paper.id, field.field_key],
    queryFn: () => apiFetch<EvidenceHistory>(`${base}/history`),
  });
  const refresh = () => {
    setEditing(false);
    void qc.invalidateQueries({ queryKey: ["evidence", projectId] });
    void qc.invalidateQueries({ queryKey: ["extraction", projectId] });
  };
  const approve = useMutation({
    mutationFn: (item: EvidenceItem) =>
      apiFetch(`${base}/approve`, { method: "POST", ...json({ expected_row_version: item.row_version }) }),
    onSuccess: refresh,
    onError: (e) => reportError(errText(e, "Could not approve the value.")),
  });
  const item = history?.item ?? null;
  return (
    <aside
      className="flex flex-col gap-3 rounded border border-line bg-panel p-4"
      aria-label="Evidence detail"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{field.label}</h3>
          <p className="text-xs text-muted">{row.paper.title}</p>
        </div>
        <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close detail">
          Close
        </button>
      </div>
      {!history ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : (
        <>
          <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted">Status</dt>
            <dd>{item ? EVIDENCE_LABELS[item.status] : "Not extracted"}</dd>
            <dt className="text-muted">Value</dt>
            <dd>{item && item.status === "found" ? formatValue(item.value) : "—"}</dd>
            <dt className="text-muted">Proposed by</dt>
            <dd>
              {item?.origin === "ai"
                ? `AI (${history.model_run?.model ?? "model run removed"}, ${item.extraction_version ?? ""})`
                : item?.origin === "system"
                  ? "Automatic (no full text)"
                  : item
                    ? "Reviewer"
                    : "—"}
            </dd>
            <dt className="text-muted">Review</dt>
            <dd>{item?.review_status ?? "—"}</dd>
            {item?.note && (
              <>
                <dt className="text-muted">Note</dt>
                <dd>{item.note}</dd>
              </>
            )}
          </dl>
          {item && item.validation.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-danger" aria-label="Validation warnings">
              {item.validation.map((w, i) => (
                <li key={i}>{w.message}</li>
              ))}
            </ul>
          )}
          <SourcePage history={history} />
          {canWrite && (
            <div className="flex flex-wrap gap-2">
              {item && item.status !== "pending" && item.review_status !== "approved" && (
                <button
                  type="button"
                  className="btn"
                  onClick={() => approve.mutate(item)}
                  disabled={approve.isPending}
                >
                  Approve value
                </button>
              )}
              <button type="button" className="btn-secondary" onClick={() => setEditing((v) => !v)}>
                {editing ? "Cancel edit" : "Correct value"}
              </button>
            </div>
          )}
          {editing && (
            <EditForm
              base={base}
              field={field}
              item={item}
              documentId={history.source_page?.document_id ?? row.paper.document_id}
              onSaved={refresh}
            />
          )}
          <div>
            <h4 className="section-title mb-1">Outputs generated from this value</h4>
            {history.outputs.length === 0 ? (
              <p className="text-xs text-muted">None yet.</p>
            ) : (
              <ul className="text-xs">
                {history.outputs.map((o) => (
                  <li key={o.id}>
                    {o.kind === "csv_export" ? "Evidence table CSV" : o.kind},{" "}
                    {new Date(o.created_at).toLocaleString()} — used revision {o.revision}
                    {o.stale ? <span className="text-danger"> · out of date, regenerate</span> : " · current"}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h4 className="section-title mb-1">History</h4>
            <ol className="text-xs">
              {history.revisions.map((r) => (
                <li key={r.revision}>
                  #{r.revision} {r.action.replace(/_/g, " ")} — {new Date(r.at).toLocaleString()}:{" "}
                  {EVIDENCE_LABELS[(r.after.status as EvidenceStatus) ?? "pending"]}
                  {r.after.value !== null &&
                    r.after.value !== undefined &&
                    ` “${formatValue(r.after.value)}”`}
                  {r.reason && ` (${r.reason})`}
                </li>
              ))}
            </ol>
          </div>
        </>
      )}
    </aside>
  );
}

export default function EvidenceTablePanel({
  projectId,
  canWrite,
}: {
  projectId: string;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const [selected, setSelected] = useState<{ paperId: string; fieldKey: string } | null>(null);
  const { data: schemas } = useQuery({
    queryKey: ["extraction", projectId, "schemas"],
    queryFn: () => apiFetch<ExtractionSchema[]>(`/projects/${projectId}/extraction/schemas`),
  });
  const schema = schemas?.filter((s) => s.status === "approved").sort((a, b) => b.version - a.version)[0];
  const sid = schema?.id;
  const { data: run } = useQuery({
    queryKey: ["evidence", projectId, "runs", sid],
    queryFn: () => apiFetch<ExtractionRunStatus>(`/projects/${projectId}/extraction/schemas/${sid}/runs`),
    enabled: !!sid,
    refetchInterval: (q) => (q.state.data?.active_job_id ? 3000 : false),
  });
  const active = !!run?.active_job_id;
  const { data: tbl } = useQuery({
    queryKey: ["evidence", projectId, "table", sid, active],
    queryFn: () =>
      apiFetch<EvidenceTable>(`/projects/${projectId}/extraction/schemas/${sid}/table?limit=200`),
    enabled: !!sid,
    refetchInterval: active ? 5000 : false,
  });
  const { data: affected } = useQuery({
    queryKey: ["evidence", projectId, "affected", sid],
    queryFn: () => apiFetch<AffectedOutputs>(`/projects/${projectId}/extraction/schemas/${sid}/affected`),
    enabled: !!sid,
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["evidence", projectId] });
  const start = useMutation({
    mutationFn: (force: boolean) =>
      apiFetch(`/projects/${projectId}/extraction/schemas/${sid}/runs`, {
        method: "POST",
        ...json({ force }),
      }),
    onSuccess: () => {
      void invalidate();
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => reportError(errText(e, "Could not start extraction.")),
  });
  const cancel = useMutation({
    mutationFn: (jobId: string) => apiFetch(`/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: invalidate,
    onError: (e) => reportError(errText(e, "Could not cancel extraction.")),
  });
  const exportCsv = useMutation({
    mutationFn: () => downloadEvidenceCsv(projectId, sid ?? ""),
    onSuccess: invalidate,
    onError: (e) => reportError(errText(e, "Could not export the evidence table.")),
  });

  if (!schemas) return <p className="text-muted">Loading…</p>;
  if (!schema || !sid)
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">No evidence table yet</h2>
        <p className="text-muted">
          Approve an extraction schema in the Extraction tab. The evidence table then lists every included
          paper with one column per field, each value linked to its page and passage.
        </p>
      </div>
    );

  const fields = schema.field_definitions;
  const sel = selected && tbl?.rows.find((r) => r.paper.id === selected.paperId);
  const selField = selected && fields.find((f) => f.field_key === selected.fieldKey);
  const last = run?.last_result;
  return (
    <div className="flex flex-col gap-4" data-testid="evidence-table-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-base font-semibold">
          Evidence table · schema v{schema.version}
          {tbl && (
            <span className="ml-2 text-xs font-normal text-muted">
              {tbl.needs_review} AI value(s) need review
            </span>
          )}
        </h2>
        {canWrite && (
          <>
            <button
              type="button"
              className="btn"
              onClick={() => start.mutate(false)}
              disabled={active || start.isPending}
            >
              Extract evidence
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => start.mutate(true)}
              disabled={active || start.isPending}
              title="Re-extract AI values nobody has reviewed yet; reviewed values are never overwritten"
            >
              Rerun unreviewed
            </button>
            {active && run?.active_job_id && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => cancel.mutate(run.active_job_id as string)}
              >
                Cancel
              </button>
            )}
          </>
        )}
        <button
          type="button"
          className="btn-secondary"
          onClick={() => exportCsv.mutate()}
          disabled={exportCsv.isPending}
        >
          Export CSV
        </button>
      </div>
      {run && <ConsentBox projectId={projectId} consent={run.consent} canWrite={canWrite} />}
      {active && (
        <p className="text-xs text-muted" role="status">
          Extraction is running in the background…
        </p>
      )}
      {!active && last?.message && (
        <p className="text-xs text-muted" role="status">
          {last.message}
        </p>
      )}
      {run?.last_error && !active && <p className="text-xs text-danger">{run.last_error}</p>}
      {affected && affected.stale_outputs.length > 0 && (
        <p className="rounded border border-danger/60 p-2 text-xs text-danger" role="alert">
          {affected.stale_outputs.length} export(s) used values that have since changed; export again to
          refresh them.
        </p>
      )}
      {affected?.superseded_schema && affected.superseded_schema.rerun_fields.length > 0 && (
        <p className="text-xs text-muted">
          Compared with schema v{affected.superseded_schema.version}, these fields are new or changed and need
          extraction: {affected.superseded_schema.rerun_fields.join(", ")}.
        </p>
      )}
      <div className="overflow-x-auto rounded border border-line">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-raised">
            <tr>
              <th scope="col" className="p-2 font-semibold">
                Paper
              </th>
              {fields.map((f) => (
                <th key={f.field_key} scope="col" className="p-2 font-semibold">
                  {f.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(tbl?.rows ?? []).map((r) => (
              <tr key={r.paper.id} className="border-t border-line align-top">
                <th scope="row" className="max-w-xs p-2 font-normal">
                  <div className="font-medium">{r.paper.title}</div>
                  <div className="text-muted">
                    {[r.paper.first_author, r.paper.year].filter(Boolean).join(", ")}
                    {!r.paper.document_id && " · no full text"}
                    {!r.paper.included && " · no longer included"}
                  </div>
                </th>
                {fields.map((f) => {
                  const cell = r.cells[f.field_key];
                  const t = cellText(cell);
                  const isSel = selected?.paperId === r.paper.id && selected.fieldKey === f.field_key;
                  return (
                    <td key={f.field_key} className="p-1">
                      <button
                        type="button"
                        className={`w-full rounded p-1 text-left hover:bg-raised ${isSel ? "ring-1 ring-mark" : ""}`}
                        onClick={() => setSelected({ paperId: r.paper.id, fieldKey: f.field_key })}
                        aria-label={`${f.label} for ${r.paper.title}: ${t.primary}. ${t.secondary}`}
                      >
                        <div className={cell?.status === "found" ? "text-ink" : "text-muted"}>
                          {t.primary}
                        </div>
                        <div className="text-2xs text-muted">
                          {t.secondary}
                          {cell && cell.validation.length > 0 && (
                            <span className="text-danger"> · {cell.validation.length} warning(s)</span>
                          )}
                        </div>
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
            {tbl && tbl.rows.length === 0 && (
              <tr>
                <td className="p-3 text-muted" colSpan={fields.length + 1}>
                  No included papers yet. Include papers during screening to extract evidence from them.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {tbl && tbl.total > tbl.rows.length && (
        <p className="text-xs text-muted">
          Showing the first {tbl.rows.length} of {tbl.total} papers. Export CSV for the complete table.
        </p>
      )}
      {sel && selField && (
        <CellDetail
          key={`${sel.paper.id}-${selField.field_key}`}
          projectId={projectId}
          schemaId={sid}
          row={sel}
          field={selField}
          canWrite={canWrite}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
