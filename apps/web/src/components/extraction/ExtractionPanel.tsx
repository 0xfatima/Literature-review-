"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import type {
  ExtractionSchema,
  FieldCoverage,
  FieldDefinition,
  FieldType,
  SchemaCoverage,
} from "@/lib/types";

const FIELD_TYPES: FieldType[] = ["text", "number", "choice", "multi_choice", "boolean", "date"];
const TYPE_LABELS: Record<FieldType, string> = {
  text: "Text",
  number: "Number",
  choice: "Single choice",
  multi_choice: "Multiple choice",
  boolean: "Yes / No",
  date: "Date",
};

const STATUS_LABELS: Record<string, string> = {
  found: "Found",
  not_located: "Not located",
  unavailable: "No full text",
  not_applicable: "N/A",
  pending: "Pending",
};

const STATUS_COLORS: Record<string, string> = {
  found: "text-ok",
  not_located: "text-mark",
  unavailable: "text-muted",
  not_applicable: "text-muted",
  pending: "text-muted",
};

function CoverageBar({ cov }: { cov: FieldCoverage }) {
  const total = cov.included_papers;
  if (total === 0) return null;
  return (
    <div className="mt-1 text-xs" aria-label={`Coverage for ${cov.label}`}>
      <div className="flex gap-3 flex-wrap">
        {Object.entries({
          found: cov.found,
          not_located: cov.not_located,
          unavailable: cov.unavailable,
          pending: cov.pending,
        })
          .filter(([, n]) => n > 0)
          .map(([k, n]) => (
            <span key={k} className={STATUS_COLORS[k]}>
              {STATUS_LABELS[k]}: {n}/{total}
            </span>
          ))}
      </div>
    </div>
  );
}

function FieldEditor({
  fields,
  onChange,
}: {
  fields: FieldDefinition[];
  onChange: (f: FieldDefinition[]) => void;
}) {
  const add = () =>
    onChange([...fields, { field_key: `field_${fields.length + 1}`, label: "", type: "text" }]);
  const update = (i: number, patch: Partial<FieldDefinition>) => {
    const next = fields.map((f, j) => (i === j ? { ...f, ...patch } : f));
    onChange(next);
  };
  const remove = (i: number) => onChange(fields.filter((_, j) => j !== i));
  const move = (from: number, to: number) => {
    if (to < 0 || to >= fields.length) return;
    const next = [...fields];
    const [item] = next.splice(from, 1);
    if (!item) return;
    next.splice(to, 0, item);
    onChange(next);
  };

  return (
    <div className="flex flex-col gap-2">
      {fields.map((f, i) => (
        <div key={i} className="rounded border border-line bg-canvas p-3">
          <div className="flex flex-wrap gap-2 items-end">
            <div className="flex flex-col gap-0.5 flex-1">
              <label className="text-2xs text-muted" htmlFor={`label-${i}`}>
                Label
              </label>
              <input
                id={`label-${i}`}
                className="input"
                value={f.label}
                onChange={(e) => update(i, { label: e.target.value })}
                placeholder="e.g. Sample size"
              />
            </div>
            <div className="flex flex-col gap-0.5">
              <label className="text-2xs text-muted" htmlFor={`key-${i}`}>
                Key
              </label>
              <input
                id={`key-${i}`}
                className="input !w-36"
                value={f.field_key}
                onChange={(e) =>
                  update(i, { field_key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })
                }
                placeholder="sample_size"
              />
            </div>
            <div className="flex flex-col gap-0.5">
              <label className="text-2xs text-muted" htmlFor={`type-${i}`}>
                Type
              </label>
              <select
                id={`type-${i}`}
                className="input !w-36"
                value={f.type}
                onChange={(e) => update(i, { type: e.target.value as FieldType })}
              >
                {FIELD_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex gap-1 pb-0.5">
              <button
                type="button"
                className="btn-ghost"
                disabled={i === 0}
                onClick={() => move(i, i - 1)}
                aria-label="Move up"
              >
                ↑
              </button>
              <button
                type="button"
                className="btn-ghost"
                disabled={i === fields.length - 1}
                onClick={() => move(i, i + 1)}
                aria-label="Move down"
              >
                ↓
              </button>
              <button
                type="button"
                className="btn-ghost text-danger"
                onClick={() => remove(i)}
                aria-label="Remove field"
              >
                ✕
              </button>
            </div>
          </div>
          {(f.type === "choice" || f.type === "multi_choice") && (
            <div className="mt-2">
              <label className="text-2xs text-muted" htmlFor={`opts-${i}`}>
                Options (one per line)
              </label>
              <textarea
                id={`opts-${i}`}
                className="input mt-0.5 w-full"
                rows={3}
                value={(f.options ?? []).join("\n")}
                onChange={(e) => update(i, { options: e.target.value.split("\n").filter(Boolean) })}
              />
            </div>
          )}
        </div>
      ))}
      <button type="button" className="btn-secondary self-start" onClick={add}>
        + Add field
      </button>
    </div>
  );
}

function CoverageView({ projectId, schema }: { projectId: string; schema: ExtractionSchema }) {
  const { data, isLoading } = useQuery({
    queryKey: ["extraction", projectId, schema.id, "coverage"],
    queryFn: () =>
      apiFetch<SchemaCoverage>(`/projects/${projectId}/extraction/schemas/${schema.id}/coverage`),
    enabled: schema.status === "approved",
  });
  if (schema.status !== "approved") return null;
  if (isLoading) return <p className="text-xs text-muted">Loading coverage…</p>;
  if (!data) return null;

  const allFields = [...Object.values(data.by_question).flatMap((q) => q.fields), ...data.unmapped_fields];

  if (allFields.length === 0) return null;

  return (
    <div className="mt-3 border-t border-line pt-3">
      <h4 className="section-title mb-2">Evidence coverage ({data.included_papers} included papers)</h4>
      {data.included_papers === 0 && (
        <p className="text-xs text-muted">
          No papers are included yet. Coverage will appear here once papers are included.
        </p>
      )}
      {data.included_papers > 0 && (
        <div className="flex flex-col gap-1">
          {allFields.map((cov) => (
            <div key={cov.field_key}>
              <p className="text-sm font-medium">{cov.label}</p>
              <CoverageBar cov={cov} />
            </div>
          ))}
          <p className="mt-1 text-2xs text-muted">
            “Not located” = full text was read but the information was not found. “No full text” = the file is
            not uploaded; this is distinct from the information being absent.
          </p>
        </div>
      )}
    </div>
  );
}

export default function ExtractionPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draftFields, setDraftFields] = useState<FieldDefinition[]>([]);
  const [error, setError] = useState<string | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["extraction", projectId] });

  const { data: schemas } = useQuery({
    queryKey: ["extraction", projectId, "schemas"],
    queryFn: () => apiFetch<ExtractionSchema[]>(`/projects/${projectId}/extraction/schemas`),
  });

  const approved = schemas?.find(
    (s) =>
      s.status === "approved" &&
      s.version === Math.max(...schemas.filter((s2) => s2.status === "approved").map((s3) => s3.version), 0),
  );
  const draft = schemas?.find((s) => s.status === "draft");

  const create = useMutation({
    mutationFn: (fields: FieldDefinition[]) =>
      apiFetch(`/projects/${projectId}/extraction/schemas`, json({ field_definitions: fields })),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      invalidate();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save the schema."),
  });

  const update = useMutation({
    mutationFn: ({ id, fields, rv }: { id: string; fields: FieldDefinition[]; rv: number }) =>
      apiFetch(`/projects/${projectId}/extraction/schemas/${id}`, {
        ...json({ field_definitions: fields, expected_row_version: rv }),
        method: "PATCH",
      }),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      invalidate();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not update the schema."),
  });

  const approve = useMutation({
    mutationFn: (s: ExtractionSchema) =>
      apiFetch(`/projects/${projectId}/extraction/schemas/${s.id}/approve`, {
        ...json({ expected_row_version: s.row_version }),
        method: "POST",
      }),
    onSuccess: () => {
      invalidate();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not approve the schema."),
  });

  const startEdit = (s: ExtractionSchema) => {
    setDraftFields(s.field_definitions);
    setEditing(true);
    setError(null);
  };

  const startNew = () => {
    setDraftFields([
      {
        field_key: "study_design",
        label: "Study design",
        type: "choice",
        options: ["RCT", "Cohort", "Case-control", "Cross-sectional", "Other"],
      },
      { field_key: "sample_size", label: "Sample size (N)", type: "number" },
      { field_key: "population", label: "Population / participants", type: "text" },
      { field_key: "intervention", label: "Intervention or exposure", type: "text" },
      { field_key: "comparator", label: "Comparator", type: "text" },
      { field_key: "primary_outcome", label: "Primary outcome", type: "text" },
      { field_key: "effect_size", label: "Effect size", type: "text" },
      { field_key: "findings", label: "Key findings", type: "text" },
      { field_key: "limitations", label: "Limitations", type: "text" },
      { field_key: "funding", label: "Funding", type: "text" },
    ]);
    setEditing(true);
    setError(null);
  };

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <div>
        <h2 className="section-title mb-1">Extraction schema</h2>
        <p className="text-sm text-muted max-w-prose">
          Define the fields to extract from each included paper. Approved schemas are immutable; create a new
          version to make changes. Evidence status distinguishes&nbsp;
          <strong>found</strong> (extracted), <strong>not located</strong> (full text read, not found),{" "}
          <strong>no full text</strong> (file not uploaded — never confused with absent evidence), and{" "}
          <strong>pending</strong>.
        </p>
      </div>

      {editing ? (
        <div className="rounded border border-line bg-panel p-4">
          <h3 className="section-title mb-3">{draft ? "Edit draft" : "New schema"}</h3>
          {error && (
            <p className="mb-2 text-sm text-danger" role="alert">
              {error}
            </p>
          )}
          <FieldEditor fields={draftFields} onChange={setDraftFields} />
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              className="btn"
              disabled={create.isPending || update.isPending}
              onClick={() => {
                if (draft) update.mutate({ id: draft.id, fields: draftFields, rv: draft.row_version });
                else create.mutate(draftFields);
              }}
            >
              {draft ? "Save draft" : "Create draft"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          {draft && (
            <div className="rounded border border-mark/50 bg-panel p-4" data-testid="draft-schema">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="section-title">Draft schema (v{draft.version})</h3>
                  <p className="text-xs text-muted">{draft.field_count} fields · not yet approved</p>
                </div>
                {canWrite && (
                  <div className="flex gap-2">
                    <button type="button" className="btn-secondary" onClick={() => startEdit(draft)}>
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={approve.isPending}
                      onClick={() => approve.mutate(draft)}
                    >
                      Approve
                    </button>
                  </div>
                )}
              </div>
              <ul className="mt-2 flex flex-col gap-0.5 text-sm">
                {draft.field_definitions.map((f) => (
                  <li key={f.field_key} className="flex items-baseline gap-2">
                    <span className="font-medium">{f.label}</span>
                    <span className="text-xs text-muted">({TYPE_LABELS[f.type]})</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {approved && (
            <div className="rounded border border-line bg-panel p-4" data-testid="approved-schema">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="section-title">Approved schema (v{approved.version})</h3>
                  <p className="text-xs text-muted">{approved.field_count} fields</p>
                </div>
                {canWrite && !draft && (
                  <button type="button" className="btn-secondary" onClick={startNew}>
                    New version
                  </button>
                )}
              </div>
              <ul className="mt-2 flex flex-col gap-0.5 text-sm">
                {approved.field_definitions.map((f) => (
                  <li key={f.field_key} className="flex items-baseline gap-2">
                    <span className="font-medium">{f.label}</span>
                    <span className="text-xs text-muted">({TYPE_LABELS[f.type]})</span>
                  </li>
                ))}
              </ul>
              <CoverageView projectId={projectId} schema={approved} />
            </div>
          )}

          {!draft && !approved && canWrite && (
            <div>
              <p className="mb-2 text-sm text-muted">
                No extraction schema yet. The default fields are based on common systematic review practice —
                customise them for your review type.
              </p>
              <button type="button" className="btn" onClick={startNew}>
                Create schema
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
