"use client";
/** Controlled editors for each protocol section. Used by the chat question cards (one section at a time) and
 *  by the Protocol tab (all sections). They hold no server state; the parent saves through the API. */
import { useId } from "react";
import { FRAMEWORKS, type FrameworkElementLike, elementRows } from "@/lib/protocol";
import type { Criterion, ProtocolQuestion } from "@/lib/types";

export function ObjectiveEditor({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="section-title mb-1 block">
        Objective
      </label>
      <textarea
        id={id}
        className="input"
        rows={3}
        maxLength={2000}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function FrameworkSelect({
  value,
  onChange,
  disabled,
}: {
  value: string | null;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="section-title mb-1 block">
        Question framework
      </label>
      <select
        id={id}
        className="input"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="" disabled>
          Choose…
        </option>
        {FRAMEWORKS.map((f) => (
          <option key={f.key} value={f.key}>
            {f.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export function ElementsEditor({
  framework,
  value,
  onChange,
  disabled,
}: {
  framework: string | null;
  value: FrameworkElementLike[];
  onChange: (v: FrameworkElementLike[]) => void;
  disabled?: boolean;
}) {
  const rows = elementRows(framework, value);
  const base = useId();
  if (rows.length === 0)
    return <p className="text-sm text-muted">No framework elements to fill in for this choice.</p>;
  const custom = framework === "custom";
  const set = (i: number, patch: Partial<FrameworkElementLike>) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="section-title mb-1">Framework elements</legend>
      {rows.map((r, i) => (
        <div key={custom ? i : r.key} className={custom ? "grid grid-cols-[10rem_1fr_auto] gap-2" : ""}>
          {custom ? (
            <>
              <label className="sr-only" htmlFor={`${base}-l${i}`}>
                Element {i + 1} name
              </label>
              <input
                id={`${base}-l${i}`}
                className="input"
                placeholder="Element name"
                value={r.label}
                maxLength={120}
                onChange={(e) => set(i, { label: e.target.value })}
              />
            </>
          ) : (
            <label htmlFor={`${base}-v${i}`} className="mb-0.5 block text-xs text-muted">
              {r.label}
            </label>
          )}
          {custom && (
            <label className="sr-only" htmlFor={`${base}-v${i}`}>
              Element {i + 1} description
            </label>
          )}
          <input
            id={`${base}-v${i}`}
            className="input"
            value={r.value ?? ""}
            maxLength={2000}
            onChange={(e) => set(i, { value: e.target.value })}
          />
          {custom && (
            <button
              type="button"
              className="btn-ghost text-xs"
              onClick={() => onChange(rows.filter((_, j) => j !== i))}
            >
              Remove
            </button>
          )}
        </div>
      ))}
      {custom && rows.length < 8 && (
        <button
          type="button"
          className="btn-ghost self-start text-xs"
          onClick={() => onChange([...rows, { key: "", label: "", value: "" }])}
        >
          Add element
        </button>
      )}
    </fieldset>
  );
}

export function QuestionsEditor({
  value,
  onChange,
  disabled,
}: {
  value: ProtocolQuestion[];
  onChange: (v: ProtocolQuestion[]) => void;
  disabled?: boolean;
}) {
  const base = useId();
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="section-title mb-1">Review questions</legend>
      {value.length === 0 && <p className="text-sm text-muted">No questions yet.</p>}
      {value.map((q, i) => (
        <div key={q.stable_key ?? `new-${i}`} className="flex gap-2">
          <label htmlFor={`${base}-${i}`} className="sr-only">
            Review question {i + 1}
          </label>
          <textarea
            id={`${base}-${i}`}
            className="input"
            rows={2}
            maxLength={2000}
            value={q.text}
            onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
          />
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        className="btn-ghost self-start text-xs"
        onClick={() => onChange([...value, { text: "" }])}
      >
        Add question
      </button>
    </fieldset>
  );
}

export interface ScopeValue {
  date_from: string;
  date_to: string;
  languages: string;
  study_designs: string;
  sources: string;
  search_strategy: string;
}

export function ScopeEditor({
  value,
  onChange,
  disabled,
}: {
  value: ScopeValue;
  onChange: (v: ScopeValue) => void;
  disabled?: boolean;
}) {
  const base = useId();
  const field = (k: keyof ScopeValue, label: string, hint?: string) => (
    <div>
      <label htmlFor={`${base}-${k}`} className="mb-0.5 block text-xs text-muted">
        {label}
        {hint && <span className="sr-only"> ({hint})</span>}
      </label>
      <input
        id={`${base}-${k}`}
        className="input"
        value={value[k]}
        placeholder={hint}
        inputMode={k.startsWith("date") ? "numeric" : undefined}
        onChange={(e) => onChange({ ...value, [k]: e.target.value })}
      />
    </div>
  );
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="section-title mb-1">Scope</legend>
      <div className="grid grid-cols-2 gap-2">
        {field("date_from", "From year", "e.g. 2000")}
        {field("date_to", "To year", "e.g. 2025")}
      </div>
      {field("languages", "Languages", "comma separated")}
      {field("study_designs", "Study designs", "comma separated")}
      {field("sources", "Sources to search", "comma separated")}
      <div>
        <label htmlFor={`${base}-ss`} className="mb-0.5 block text-xs text-muted">
          Search strategy notes
        </label>
        <textarea
          id={`${base}-ss`}
          className="input"
          rows={2}
          maxLength={8000}
          value={value.search_strategy}
          onChange={(e) => onChange({ ...value, search_strategy: e.target.value })}
        />
      </div>
    </fieldset>
  );
}

export function CriteriaEditor({
  value,
  onChange,
  disabled,
}: {
  value: Criterion[];
  onChange: (v: Criterion[]) => void;
  disabled?: boolean;
}) {
  const base = useId();
  const set = (i: number, patch: Partial<Criterion>) =>
    onChange(value.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="section-title mb-1">Eligibility criteria</legend>
      <ul aria-label="Criteria" className="flex flex-col gap-2">
        {value.map((c, i) => {
          const suggested = c.origin === "suggested";
          const state = c.state ?? "accepted";
          return (
            <li
              key={c.stable_key ?? `new-${i}`}
              className={`rounded border px-2 py-2 ${state === "rejected" ? "border-line opacity-60" : "border-line"}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor={`${base}-k${i}`} className="sr-only">
                  Criterion {i + 1} type
                </label>
                <select
                  id={`${base}-k${i}`}
                  className="input w-32 py-1"
                  value={c.kind}
                  onChange={(e) => set(i, { kind: e.target.value as Criterion["kind"] })}
                >
                  <option value="inclusion">Include</option>
                  <option value="exclusion">Exclude</option>
                </select>
                <label htmlFor={`${base}-t${i}`} className="sr-only">
                  Criterion {i + 1}
                </label>
                <input
                  id={`${base}-t${i}`}
                  className="input min-w-0 flex-1 py-1"
                  value={c.text}
                  maxLength={2000}
                  onChange={(e) => set(i, { text: e.target.value })}
                />
                <button
                  type="button"
                  className="btn-ghost text-xs"
                  onClick={() => onChange(value.filter((_, j) => j !== i))}
                >
                  Remove
                </button>
              </div>
              {suggested && (
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-muted">
                    Suggestion
                    {state === "proposed"
                      ? " (undecided)"
                      : state === "accepted"
                        ? " (accepted)"
                        : " (rejected)"}
                    {c.rationale ? `: ${c.rationale}` : ""}
                  </span>
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    aria-pressed={state === "accepted"}
                    onClick={() => set(i, { state: "accepted" })}
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    aria-pressed={state === "rejected"}
                    onClick={() => set(i, { state: "rejected" })}
                  >
                    Reject
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        className="btn-ghost self-start text-xs"
        onClick={() =>
          onChange([...value, { kind: "inclusion", text: "", origin: "user", state: "accepted" }])
        }
      >
        Add criterion
      </button>
    </fieldset>
  );
}
