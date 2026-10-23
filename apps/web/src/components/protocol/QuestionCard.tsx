"use client";
/** The interactive card for the workflow's pending question, shown under that question in the chat. */
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { SECTION_LABELS, mergeCriteria, parseList, parseYear, undecided, withText } from "@/lib/protocol";
import type { Criterion, PendingQuestion, ProtocolOverview, ProtocolQuestion } from "@/lib/types";
import type { FrameworkElementLike } from "@/lib/protocol";
import {
  CriteriaEditor,
  ElementsEditor,
  FrameworkSelect,
  ObjectiveEditor,
  QuestionsEditor,
  ScopeEditor,
  type ScopeValue,
} from "./Editors";
import { useAnswer } from "./hooks";
import ProtocolSummary from "./ProtocolSummary";

export function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "Network error. Check your connection.";
  if (e.code === "version_conflict")
    return "The protocol changed elsewhere. The latest version is loaded; try again.";
  const details = Array.isArray(e.details) ? (e.details as { field?: string; issue?: string }[]) : [];
  const extra = details
    .filter((d) => d.field)
    .map((d) => `${d.field}${d.issue ? `: ${d.issue}` : ""}`)
    .join("; ");
  return extra ? `${e.message} (${extra})` : e.message;
}

export default function QuestionCard({
  projectId,
  question,
  protocol,
  disabled,
}: {
  projectId: string;
  question: PendingQuestion;
  protocol: ProtocolOverview;
  disabled: boolean;
}) {
  const draft = protocol.draft;
  const c = draft?.content;
  const answer = useAnswer(projectId);
  const [objective, setObjective] = useState(c?.objective ?? "");
  const [framework, setFramework] = useState<string | null>(c?.framework ?? null);
  const [elements, setElements] = useState<FrameworkElementLike[]>(c?.framework_elements ?? []);
  const [questions, setQuestions] = useState<ProtocolQuestion[]>(
    c?.questions.length
      ? c.questions
      : question.data?.suggested_question
        ? [{ text: question.data.suggested_question }]
        : [],
  );
  const [scope, setScope] = useState<ScopeValue>({
    date_from: c?.date_from?.toString() ?? "",
    date_to: c?.date_to?.toString() ?? "",
    languages: (c?.languages ?? []).join(", "),
    study_designs: (c?.study_designs ?? []).join(", "),
    sources: (c?.sources ?? []).join(", "),
    search_strategy: c?.search_strategy ?? "",
  });
  const [criteria, setCriteria] = useState<Criterion[]>(
    mergeCriteria(c?.criteria ?? [], question.data?.suggestions),
  );
  const [section, setSection] = useState("criteria");
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => setLocalError(null), [question.id]);

  const send = (action: string, values: Record<string, unknown> = {}) => {
    setLocalError(null);
    answer.mutate({
      question_id: question.id,
      action,
      values,
      expected_row_version: draft?.row_version ?? null,
    });
  };
  const busy = disabled || answer.isPending;
  const err = localError ?? (answer.isError ? errorText(answer.error) : null);

  const saveSkip = (onSave: () => void, saveLabel = "Save and continue") => (
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" className="btn" disabled={busy} onClick={onSave}>
        {saveLabel}
      </button>
      <button type="button" className="btn-secondary" disabled={busy} onClick={() => send("skip")}>
        Skip for now
      </button>
    </div>
  );

  let body: React.ReactNode = null;
  switch (question.kind) {
    case "objective":
      body = (
        <>
          <ObjectiveEditor value={objective} onChange={setObjective} disabled={busy} />
          {saveSkip(() => send("save", { objective }))}
        </>
      );
      break;
    case "framework": {
      const s = question.data?.suggestion;
      body = (
        <>
          {s && (
            <div className="mb-3 rounded border border-line p-2 text-sm">
              <p>
                Suggested: <strong>{s.label}</strong>
              </p>
              <p className="text-muted">{s.rationale}</p>
              <p className="mt-1 text-2xs text-muted">{s.method_note}</p>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {s && s.framework !== "custom" && (
              <button type="button" className="btn" disabled={busy} onClick={() => send("accept")}>
                Use {s.label}
              </button>
            )}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div className="min-w-48">
              <FrameworkSelect value={framework} onChange={setFramework} disabled={busy} />
            </div>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !framework}
              onClick={() => send("choose", { framework })}
            >
              Use selected framework
            </button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => send("skip")}>
              No framework
            </button>
          </div>
        </>
      );
      break;
    }
    case "elements":
      body = (
        <>
          <ElementsEditor
            framework={c?.framework ?? null}
            value={elements}
            onChange={setElements}
            disabled={busy}
          />
          {saveSkip(() =>
            send("save", {
              framework_elements: (elements.length ? elements : []).filter((e) => e.label || e.key),
            }),
          )}
        </>
      );
      break;
    case "questions":
      body = (
        <>
          {question.data?.suggested_question && (
            <p className="mb-2 text-xs text-muted">
              A draft question was assembled from your framework answers. Edit it freely.
            </p>
          )}
          <QuestionsEditor value={questions} onChange={setQuestions} disabled={busy} />
          {saveSkip(() => send("save", { questions: withText(questions) }))}
        </>
      );
      break;
    case "scope":
      body = (
        <>
          <ScopeEditor value={scope} onChange={setScope} disabled={busy} />
          {saveSkip(() => {
            const df = parseYear(scope.date_from);
            const dt = parseYear(scope.date_to);
            if (Number.isNaN(df) || Number.isNaN(dt)) return setLocalError("Years must be whole numbers.");
            send("save", {
              date_from: df,
              date_to: dt,
              languages: parseList(scope.languages),
              study_designs: parseList(scope.study_designs),
              sources: parseList(scope.sources),
              search_strategy: scope.search_strategy || null,
            });
          })}
        </>
      );
      break;
    case "criteria":
      body = (
        <>
          <CriteriaEditor value={criteria} onChange={setCriteria} disabled={busy} />
          {undecided(criteria) > 0 && (
            <p className="mt-2 text-xs text-muted">
              {undecided(criteria)} suggestion(s) undecided. You can save now, but every suggestion must be
              accepted or rejected before the protocol can be approved.
            </p>
          )}
          {saveSkip(() => send("save", { criteria: withText(criteria) }))}
        </>
      );
      break;
    case "review":
      body = draft ? (
        <>
          <ProtocolSummary version={draft} />
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <button
              type="button"
              className="btn"
              disabled={busy || !draft.can_approve}
              onClick={() => send("approve")}
            >
              Approve protocol version {draft.version}
            </button>
            <div>
              <label htmlFor={`edit-${question.id}`} className="sr-only">
                Section to edit
              </label>
              <select
                id={`edit-${question.id}`}
                className="input py-1"
                value={section}
                onChange={(e) => setSection(e.target.value)}
              >
                {Object.entries(SECTION_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => send("edit", { section })}
            >
              Edit this section
            </button>
          </div>
          {!draft.can_approve && (
            <p className="mt-2 text-xs text-muted">Resolve the items marked “must fix” to enable approval.</p>
          )}
        </>
      ) : (
        <p className="text-muted">Loading the protocol…</p>
      );
      break;
  }

  return (
    <section
      aria-label="Workflow question"
      className="mt-2 rounded border border-mark/50 bg-canvas/40 p-3"
      data-testid="question-card"
      data-kind={question.kind}
    >
      <p className="mb-2 text-xs text-muted">{question.help}</p>
      {body}
      {err && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {err}
        </p>
      )}
      {answer.isPending && <p className="mt-2 text-xs text-muted">Saving…</p>}
    </section>
  );
}
