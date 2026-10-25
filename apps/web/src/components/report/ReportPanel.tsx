"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useStatus } from "@/components/status/StatusContext";
import { useUnsavedChanges } from "@/components/workspace/WorkspaceContext";
import { ApiError, apiFetch, json } from "@/lib/api";
import { formatValue } from "@/lib/extraction";
import {
  EXPORT_FORMATS,
  displayText,
  downloadExport,
  isEditable,
  isPlaceholder,
  issuesFor,
} from "@/lib/report";
import { CERTAINTY_LABELS, CERTAINTY_TONE, humanize } from "@/lib/synthesis";
import type {
  ClaimTrace,
  ReportBlock,
  ReportExportRecord,
  ReportIssue,
  ReportVersion,
  ReportVersionSummary,
} from "@/lib/types";

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
type Op = Record<string, unknown>;

function Trace({ projectId, draftId, claimId }: { projectId: string; draftId: string; claimId: string }) {
  const { data } = useQuery({
    queryKey: ["report", projectId, "trace", draftId, claimId],
    queryFn: () =>
      apiFetch<ClaimTrace>(
        `/projects/${projectId}/reports/${draftId}/trace?claim_id=${encodeURIComponent(claimId)}`,
      ),
  });
  if (!data) return <p className="text-xs text-muted">Loading sources…</p>;
  return (
    <div className="mt-2 rounded border border-line bg-canvas p-2 text-xs" aria-label="Sources of this claim">
      {data.source_explanation && <p className="text-muted">{data.source_explanation}</p>}
      {data.facts && (
        <p className="text-muted">
          Recorded counts:{" "}
          {Object.entries(data.facts)
            .filter(([, v]) => typeof v === "number")
            .map(([k, v]) => `${humanize(k)} ${String(v)}`)
            .join(" · ")}
        </p>
      )}
      {data.references.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {data.references.map((r) => (
            <li key={r.paper_id}>
              [{r.key}] {r.formatted}
              {r.is_retracted && <span className="text-danger"> (flagged as retracted)</span>}
            </li>
          ))}
        </ul>
      )}
      {data.evidence.length > 0 && (
        <ul className="mt-1 flex flex-col gap-1">
          {data.evidence.map((e) => (
            <li key={e.evidence_item_id}>
              {humanize(e.field_key)}: {e.status === "found" ? formatValue(e.value) : humanize(e.status)}
              {e.page_no && ` · p. ${e.page_no}`}
              {e.excerpt && <span className="block text-muted">“{e.excerpt}”</span>}
              {e.changed_since_cited && <span className="text-danger"> · changed since this draft</span>}
            </li>
          ))}
        </ul>
      )}
      {data.references.length === 0 && data.evidence.length === 0 && !data.facts && (
        <p className="text-muted">No linked records.</p>
      )}
    </div>
  );
}

function Block({
  b,
  issues,
  projectId,
  draftId,
  editing,
  draftText,
  onText,
  onRemove,
}: {
  b: ReportBlock;
  issues: ReportIssue[];
  projectId: string;
  draftId: string;
  editing: boolean;
  draftText: string | undefined;
  onText: (t: string) => void;
  onRemove: () => void;
}) {
  const [trace, setTrace] = useState(false);
  const mine = issuesFor(issues, b.id);
  return (
    <div className="rounded border border-transparent p-2 hover:border-line" id={`block-${b.id}`}>
      {b.type === "table" ? (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <caption className="py-1 text-left font-semibold">{b.table.caption}</caption>
            <thead className="bg-raised">
              <tr>
                {b.table.columns.map((c) => (
                  <th key={c} className="p-1">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.table.rows.map((r, i) => (
                <tr key={i} className="border-t border-line align-top">
                  {r.map((c, j) => (
                    <td key={j} className="p-1">
                      {c === null ? "" : String(c)}
                    </td>
                  ))}
                </tr>
              ))}
              {b.table.rows.length === 0 && (
                <tr>
                  <td className="p-1 text-muted" colSpan={b.table.columns.length}>
                    No rows recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <p className="text-2xs text-muted">Generated from stored data; not editable.</p>
        </div>
      ) : b.type === "references" ? null : editing ? (
        <textarea
          className="input"
          rows={Math.min(10, Math.max(2, Math.ceil(b.text.length / 90)))}
          value={draftText ?? b.text}
          onChange={(e) => onText(e.target.value)}
          aria-label="Edit text"
        />
      ) : (
        <p className={isPlaceholder(b.text) ? "italic text-muted" : ""}>
          {displayText(b.text)}
          {b.type === "statement" && b.certainty && (
            <span className={`ml-1 text-2xs ${CERTAINTY_TONE[b.certainty]}`}>
              ({CERTAINTY_LABELS[b.certainty]})
            </span>
          )}
        </p>
      )}
      {b.type !== "references" && (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-muted">
          {"origin" in b && (
            <span>{b.origin === "edited" ? "Edited by author" : "Generated from review data"}</span>
          )}
          <button
            type="button"
            className="text-link"
            onClick={() => setTrace((v) => !v)}
            aria-expanded={trace}
          >
            {trace ? "Hide sources" : "Show sources"}
          </button>
          {editing && isEditable(b) && (
            <button type="button" className="text-danger" onClick={onRemove}>
              Remove
            </button>
          )}
        </div>
      )}
      {mine.length > 0 && (
        <ul className="mt-1 text-xs">
          {mine.map((i, k) => (
            <li key={k} className={i.severity === "error" ? "text-danger" : "text-mark"}>
              {i.severity === "error" ? "Error" : "Check"}: {i.message}
            </li>
          ))}
        </ul>
      )}
      {trace && <Trace projectId={projectId} draftId={draftId} claimId={b.id} />}
    </div>
  );
}

export default function ReportPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [ops, setOps] = useState<Op[]>([]);
  const [summary, setSummary] = useState("");
  const [newPara, setNewPara] = useState<Record<string, string>>({});
  useUnsavedChanges(
    "report-edits",
    editing &&
      (Object.keys(texts).length > 0 ||
        ops.length > 0 ||
        Object.values(newPara).some((t) => t.trim()) ||
        !!summary),
  );
  const { data: versions } = useQuery({
    queryKey: ["report", projectId, "versions"],
    queryFn: () => apiFetch<ReportVersionSummary[]>(`/projects/${projectId}/reports`),
  });
  const draftId = selected ?? versions?.[0]?.id ?? null;
  const { data: rep } = useQuery({
    queryKey: ["report", projectId, "version", draftId],
    queryFn: () => apiFetch<ReportVersion>(`/projects/${projectId}/reports/${draftId}`),
    enabled: !!draftId,
  });
  const { data: exports } = useQuery({
    queryKey: ["report", projectId, "exports", draftId],
    queryFn: () => apiFetch<ReportExportRecord[]>(`/projects/${projectId}/reports/${draftId}/exports`),
    enabled: !!draftId,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["report", projectId] });
  const reset = () => {
    setEditing(false);
    setTexts({});
    setOps([]);
    setSummary("");
    setNewPara({});
  };
  const generate = useMutation({
    mutationFn: () =>
      apiFetch<ReportVersion>(`/projects/${projectId}/reports`, { method: "POST", ...json({}) }),
    onSuccess: (v) => {
      reset();
      setSelected(v.id);
      refresh();
    },
    onError: (e) => reportError(errText(e, "Could not generate the report.")),
  });
  const save = useMutation({
    mutationFn: (all: Op[]) =>
      apiFetch<ReportVersion>(`/projects/${projectId}/reports/${draftId}/edits`, {
        method: "POST",
        ...json({
          ops: all,
          change_summary: summary || null,
          expected_latest_version: versions?.[0]?.version,
        }),
      }),
    onSuccess: (v) => {
      reset();
      setSelected(v.id);
      refresh();
    },
    onError: (e) => reportError(errText(e, "Could not save the edits.")),
  });
  const exp = useMutation({
    mutationFn: (format: string) => downloadExport(projectId, draftId ?? "", format),
    onSuccess: refresh,
    onError: (e) => reportError(errText(e, "Could not export the report.")),
  });

  if (!versions) return <p className="text-muted">Loading…</p>;
  if (versions.length === 0)
    return (
      <div className="max-w-prose py-10">
        <h2 className="mb-2 text-base font-semibold">No report draft yet</h2>
        <p className="mb-3 text-muted">
          Generate a structured draft from the approved protocol, recorded searches, PRISMA counts, reviewed
          extraction data, approved appraisals and the accepted synthesis. Tables and counts come from stored
          data; text the authors must write is marked as a placeholder.
        </p>
        {canWrite && (
          <button
            type="button"
            className="btn"
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
          >
            Generate draft
          </button>
        )}
      </div>
    );
  const pending = (): Op[] => [
    ...Object.entries(texts).map(([block_id, text]) => ({ op: "set_text", block_id, text })),
    ...Object.entries(newPara)
      .filter(([, t]) => t.trim())
      .map(([section, text]) => ({ op: "add_paragraph", section, text })),
    ...ops,
  ];
  const s = rep?.validation.summary;
  return (
    <div className="flex flex-col gap-4" data-testid="report-panel">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-2xs text-muted" htmlFor="report-version">
          Version
        </label>
        <select
          id="report-version"
          className="input !w-72"
          value={draftId ?? ""}
          onChange={(e) => {
            reset();
            setSelected(e.target.value);
          }}
        >
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.version} · {v.status === "current" ? "current" : "earlier"} · {v.change_summary ?? ""}
            </option>
          ))}
        </select>
        <span className="mr-auto" />
        {canWrite && (
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => generate.mutate()}
              disabled={generate.isPending}
            >
              Regenerate from current data
            </button>
            {!editing ? (
              <button type="button" className="btn-secondary" onClick={() => setEditing(true)}>
                Edit
              </button>
            ) : (
              <button type="button" className="btn-ghost" onClick={reset}>
                Discard edits
              </button>
            )}
          </>
        )}
      </div>
      {rep && s && (
        <div
          className={`rounded border p-3 text-sm ${s.errors ? "border-danger/60" : "border-line"}`}
          role="status"
          aria-label="Verification status"
        >
          <p className="font-semibold">{s.label}</p>
          <p className="text-xs text-muted">
            {s.errors} error(s) · {s.warnings} warning(s) · {s.sections_verified}/{s.sections_total} sections
            verified by a person. This application never marks a review as publication-ready.
          </p>
          {rep.data_changed_since_generation && (
            <p className="mt-1 text-xs text-danger">
              The review data changed after this draft was generated. Regenerate, or check the flagged claims.
            </p>
          )}
        </div>
      )}
      {rep && (
        <>
          {editing && (
            <div className="flex flex-wrap items-end gap-2 rounded border border-line p-3">
              <label className="flex flex-1 flex-col text-2xs text-muted">
                Change summary (kept with the version)
                <input className="input" value={summary} onChange={(e) => setSummary(e.target.value)} />
              </label>
              <button
                type="button"
                className="btn"
                disabled={save.isPending || pending().length === 0}
                onClick={() => save.mutate(pending())}
              >
                Save as new version
              </button>
            </div>
          )}
          <h2 className="text-lg font-semibold">{rep.title}</h2>
          {rep.sections.map((sec) => (
            <section key={sec.key} aria-label={sec.title} className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2 border-b border-line pb-1">
                <h3 className="mr-auto text-base font-semibold">{sec.title}</h3>
                <span className="text-2xs text-muted">
                  {sec.verified_by ? `Verified ${sec.verified_at?.slice(0, 10) ?? ""}` : "Not verified"}
                </span>
                {canWrite && (
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={editing || save.isPending}
                    onClick={() =>
                      save.mutate([{ op: "verify_section", section: sec.key, verified: !sec.verified_by }])
                    }
                  >
                    {sec.verified_by ? "Withdraw verification" : "I verified this section"}
                  </button>
                )}
              </div>
              {rep.validation.issues
                .filter((i) => i.section === sec.key && !i.block_id)
                .map((i, k) => (
                  <p key={k} className={`text-xs ${i.severity === "error" ? "text-danger" : "text-mark"}`}>
                    {i.message}
                  </p>
                ))}
              {sec.blocks.map((b) =>
                b.type === "references" ? (
                  <ol key={b.id} className="list-none text-sm">
                    {rep.references.map((r) => (
                      <li key={r.key}>
                        [{r.key}] {r.formatted}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <Block
                    key={b.id}
                    b={b}
                    issues={rep.validation.issues}
                    projectId={projectId}
                    draftId={rep.id}
                    editing={editing}
                    draftText={texts[b.id]}
                    onText={(t) => setTexts((m) => ({ ...m, [b.id]: t }))}
                    onRemove={() => setOps((o) => [...o, { op: "remove_block", block_id: b.id }])}
                  />
                ),
              )}
              {editing && sec.key !== "references" && (
                <textarea
                  className="input"
                  rows={2}
                  placeholder="Add a paragraph (cite stored references as [@key])"
                  value={newPara[sec.key] ?? ""}
                  onChange={(e) => setNewPara((m) => ({ ...m, [sec.key]: e.target.value }))}
                  aria-label={`New paragraph in ${sec.title}`}
                />
              )}
            </section>
          ))}
          <section aria-label="Exports" className="flex flex-col gap-2 rounded border border-line p-3">
            <h3 className="text-sm font-semibold">Export this version</h3>
            <div className="flex flex-wrap gap-2">
              {EXPORT_FORMATS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  className="btn-secondary"
                  onClick={() => exp.mutate(f.value)}
                  disabled={exp.isPending}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted">
              Every export carries the verification notice and is recorded with its version, file hash and the
              review state it was built from.
            </p>
            {exports && exports.length > 0 && (
              <ul className="text-xs">
                {exports.map((e) => (
                  <li key={e.id}>
                    {new Date(e.created_at).toLocaleString()} · {e.filename} · sha256 {e.sha256.slice(0, 12)}…
                    · {e.validation_summary.label}
                    {e.data_changed_since_draft && (
                      <span className="text-danger"> · data had changed since the draft</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
