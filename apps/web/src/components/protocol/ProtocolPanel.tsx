"use client";
/** Protocol tab: the structured editor for the current draft, the approved version, revisions with a reason,
 *  and the version history with each revision's impact. Every action calls the real API. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch, json } from "@/lib/api";
import { frameworkLabel, impactText, isBusy, parseList, parseYear, withText } from "@/lib/protocol";
import type { FrameworkElementLike } from "@/lib/protocol";
import type {
  Criterion,
  ProtocolOverview,
  ProtocolQuestion,
  ProtocolVersion,
  WorkflowState,
} from "@/lib/types";
import {
  CriteriaEditor,
  ElementsEditor,
  FrameworkSelect,
  ObjectiveEditor,
  QuestionsEditor,
  ScopeEditor,
  type ScopeValue,
} from "./Editors";
import { useProtocol, useRefreshReview, useWorkflow } from "./hooks";
import ProtocolSummary from "./ProtocolSummary";
import QuestionCard, { errorText } from "./QuestionCard";

function DraftEditor({
  projectId,
  draft,
  disabled,
}: {
  projectId: string;
  draft: ProtocolVersion;
  disabled: boolean;
}) {
  const refresh = useRefreshReview(projectId);
  const qc = useQueryClient();
  const c = draft.content;
  const [objective, setObjective] = useState(c.objective ?? "");
  const [framework, setFramework] = useState<string | null>(c.framework);
  const [elements, setElements] = useState<FrameworkElementLike[]>(c.framework_elements);
  const [questions, setQuestions] = useState<ProtocolQuestion[]>(c.questions);
  const [criteria, setCriteria] = useState<Criterion[]>(c.criteria);
  const [scope, setScope] = useState<ScopeValue>({
    date_from: c.date_from?.toString() ?? "",
    date_to: c.date_to?.toString() ?? "",
    languages: c.languages.join(", "),
    study_designs: c.study_designs.join(", "),
    sources: c.sources.join(", "),
    search_strategy: c.search_strategy ?? "",
  });
  const [extraction, setExtraction] = useState(c.extraction_plan ?? "");
  const [synthesis, setSynthesis] = useState(c.synthesis_plan ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  // A newer server version (e.g. saved from the chat) replaces local fields.
  useEffect(() => {
    setObjective(c.objective ?? "");
    setFramework(c.framework);
    setElements(c.framework_elements);
    setQuestions(c.questions);
    setCriteria(c.criteria);
    setScope({
      date_from: c.date_from?.toString() ?? "",
      date_to: c.date_to?.toString() ?? "",
      languages: c.languages.join(", "),
      study_designs: c.study_designs.join(", "),
      sources: c.sources.join(", "),
      search_strategy: c.search_strategy ?? "",
    });
    setExtraction(c.extraction_plan ?? "");
    setSynthesis(c.synthesis_plan ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.id, draft.row_version]);

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<ProtocolVersion>(`/projects/${projectId}/protocol/draft`, { method: "PATCH", ...json(body) }),
    // Use the saved version at once: an approval clicked before the refetch lands must carry the NEW row_version.
    onSuccess: (saved) =>
      qc.setQueryData<ProtocolOverview>(["protocol", projectId], (old) =>
        old ? { ...old, draft: saved } : old,
      ),
    onSettled: refresh,
  });

  function submit() {
    setLocalError(null);
    const df = parseYear(scope.date_from);
    const dt = parseYear(scope.date_to);
    if (Number.isNaN(df) || Number.isNaN(dt)) return setLocalError("Years must be whole numbers.");
    const changedFramework = framework !== c.framework;
    save.mutate({
      expected_row_version: draft.row_version,
      objective: objective || null,
      framework,
      ...(changedFramework ? { framework_source: framework ? "user" : null } : {}),
      framework_elements: changedFramework ? [] : elements.filter((e) => e.label || e.key),
      questions: withText(questions),
      criteria: withText(criteria),
      date_from: df,
      date_to: dt,
      languages: parseList(scope.languages),
      study_designs: parseList(scope.study_designs),
      sources: parseList(scope.sources),
      search_strategy: scope.search_strategy || null,
      extraction_plan: extraction || null,
      synthesis_plan: synthesis || null,
    });
  }

  const busy = disabled || save.isPending;
  return (
    <form
      aria-label={`Edit protocol version ${draft.version}`}
      className="flex flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <ObjectiveEditor value={objective} onChange={setObjective} disabled={busy} />
      <div>
        <FrameworkSelect value={framework} onChange={setFramework} disabled={busy} />
        {draft.framework_rationale && c.framework_source === "suggested" && (
          <p className="mt-1 text-xs text-muted">Suggestion shown when chosen: {draft.framework_rationale}</p>
        )}
      </div>
      {framework === c.framework ? (
        <ElementsEditor framework={framework} value={elements} onChange={setElements} disabled={busy} />
      ) : (
        <p className="text-sm text-muted">
          Save to switch frameworks; elements of the old framework are cleared.
        </p>
      )}
      <QuestionsEditor value={questions} onChange={setQuestions} disabled={busy} />
      <ScopeEditor value={scope} onChange={setScope} disabled={busy} />
      <CriteriaEditor value={criteria} onChange={setCriteria} disabled={busy} />
      {(["extraction", "synthesis"] as const).map((k) => (
        <div key={k}>
          <label htmlFor={`plan-${k}`} className="section-title mb-1 block">
            {k === "extraction" ? "Extraction plan" : "Synthesis plan"} (optional)
          </label>
          <textarea
            id={`plan-${k}`}
            className="input"
            rows={2}
            maxLength={8000}
            value={k === "extraction" ? extraction : synthesis}
            disabled={busy}
            onChange={(e) => (k === "extraction" ? setExtraction : setSynthesis)(e.target.value)}
          />
        </div>
      ))}
      <div className="flex items-center gap-3">
        <button className="btn" disabled={busy}>
          Save protocol draft
        </button>
        {save.isSuccess && !save.isPending && <span className="text-xs text-muted">Saved</span>}
      </div>
      {(localError || save.isError) && (
        <p role="alert" className="text-sm text-danger">
          {localError ?? errorText(save.error)}
        </p>
      )}
    </form>
  );
}

function VersionHistory({ projectId, overview }: { projectId: string; overview: ProtocolOverview }) {
  const [open, setOpen] = useState<string | null>(null);
  const detail = useQuery({
    queryKey: ["protocol-version", projectId, open],
    queryFn: () => apiFetch<ProtocolVersion>(`/projects/${projectId}/protocol/versions/${open}`),
    enabled: open !== null,
  });
  return (
    <section aria-labelledby="hist-h">
      <h2 id="hist-h" className="section-title mb-2">
        Version history
      </h2>
      <ol aria-label="Protocol versions" className="flex flex-col gap-1 text-sm">
        {overview.versions.map((v) => (
          <li key={v.id}>
            <button
              type="button"
              className="btn-ghost w-full justify-start text-left"
              aria-expanded={open === v.id}
              onClick={() => setOpen(open === v.id ? null : v.id)}
            >
              Version {v.version}: {v.status}
              {v.approved_at && `, approved ${new Date(v.approved_at).toLocaleString()}`}
              {v.change_reason && ` (reason: ${v.change_reason})`}
            </button>
            {open === v.id && detail.data && (
              <div className="mb-2 ml-2 border-l border-line pl-3">
                {detail.data.impact && (
                  <p className="mb-2 text-sm" data-testid="impact">
                    Compared with version {detail.data.impact.compared_with_version}:{" "}
                    {impactText(detail.data.impact.affected_stages, overview.stage_labels)}
                  </p>
                )}
                {detail.data.content_hash && (
                  <p className="mb-1 text-2xs text-muted">
                    Content fingerprint {detail.data.content_hash.slice(0, 12)}
                  </p>
                )}
                <ProtocolSummary version={detail.data} />
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function ProtocolPanel({
  projectId,
  canWrite,
  onOpenConversation,
}: {
  projectId: string;
  canWrite: boolean;
  onOpenConversation: (id: string) => void;
}) {
  const wf = useWorkflow(projectId);
  const protocol = useProtocol(projectId);
  const refresh = useRefreshReview(projectId);
  const [reason, setReason] = useState("");
  const run = wf.data?.run ?? null;

  const start = useMutation({
    mutationFn: () => apiFetch<WorkflowState>(`/projects/${projectId}/workflow`, { method: "POST" }),
    onSuccess: (s) => s.run?.conversation_id && onOpenConversation(s.run.conversation_id),
    onSettled: refresh,
  });
  const revise = useMutation({
    mutationFn: () =>
      apiFetch<WorkflowState>(`/projects/${projectId}/protocol/revisions`, {
        method: "POST",
        ...json({ reason }),
      }),
    onSuccess: (s) => {
      setReason("");
      if (s.run?.conversation_id) onOpenConversation(s.run.conversation_id);
    },
    onSettled: refresh,
  });

  if (wf.isPending || protocol.isPending) return <p className="text-muted">Loading the protocol…</p>;
  if (wf.isError || protocol.isError)
    return (
      <p role="alert" className="text-danger">
        Could not load the protocol.
      </p>
    );
  const overview = protocol.data;
  const { draft, approved } = overview;

  if (!run)
    return (
      <div className="max-w-prose py-6">
        <h2 className="mb-2 text-base font-semibold">No protocol yet</h2>
        <p className="mb-3 text-muted">
          The protocol fixes the review&apos;s objective, questions, framework, scope and eligibility criteria
          before any search. Setup is a short conversation; you can pause and return at any time.
        </p>
        <button
          type="button"
          className="btn"
          disabled={!canWrite || start.isPending}
          onClick={() => start.mutate()}
        >
          Start protocol setup
        </button>
        {start.isError && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {errorText(start.error)}
          </p>
        )}
      </div>
    );

  const pending = run.status === "awaiting_input" ? run.pending_question : null;
  return (
    <div className="flex max-w-3xl flex-col gap-8">
      {draft && (
        <section aria-labelledby="draft-h">
          <h2 id="draft-h" className="mb-1 text-base font-semibold">
            Draft protocol, version {draft.version}
          </h2>
          {draft.change_reason && (
            <p className="mb-2 text-sm text-muted">Reason for this revision: {draft.change_reason}</p>
          )}
          <p className="mb-4 text-sm text-muted">
            Edit any field here or answer the questions in the Protocol setup chat; both save to the same
            draft. Approval happens at the review step.
          </p>
          {pending?.kind === "review" && (
            <div className="mb-6">
              <h3 className="section-title mb-1">Review and approve</h3>
              <QuestionCard
                projectId={projectId}
                question={pending}
                protocol={overview}
                disabled={!canWrite}
              />
            </div>
          )}
          <DraftEditor projectId={projectId} draft={draft} disabled={!canWrite || isBusy(run)} />
        </section>
      )}

      {approved && (
        <section aria-labelledby="approved-h">
          <h2 id="approved-h" className="mb-1 text-base font-semibold">
            Approved protocol, version {approved.version}
          </h2>
          <p className="mb-2 text-sm text-muted">
            Approved {approved.approved_at && new Date(approved.approved_at).toLocaleString()}. Framework:{" "}
            {frameworkLabel(approved.content.framework)}. This version is locked; searching and screening use
            it.
          </p>
          <ProtocolSummary version={approved} />
          {!draft && (
            <form
              className="mt-4 flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                revise.mutate();
              }}
            >
              <label htmlFor="revise-reason" className="section-title">
                Reason for revising the protocol
              </label>
              <textarea
                id="revise-reason"
                className="input"
                rows={2}
                maxLength={2000}
                value={reason}
                disabled={!canWrite}
                onChange={(e) => setReason(e.target.value)}
              />
              <p className="text-xs text-muted">
                A revision creates a new version; the approved one is kept unchanged. After approval you will
                see which stages may need rerunning.
              </p>
              <button
                className="btn-secondary self-start"
                disabled={!canWrite || reason.trim().length < 3 || revise.isPending || run.status !== "idle"}
              >
                Start a revision
              </button>
              {revise.isError && (
                <p role="alert" className="text-sm text-danger">
                  {errorText(revise.error)}
                </p>
              )}
            </form>
          )}
        </section>
      )}

      <VersionHistory projectId={projectId} overview={overview} />
    </div>
  );
}
