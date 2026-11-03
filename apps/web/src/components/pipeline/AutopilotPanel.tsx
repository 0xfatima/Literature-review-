"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import ApprovalPrompt from "@/components/common/ApprovalPrompt";
import { useStatus } from "@/components/status/StatusContext";
import { useWorkspace } from "@/components/workspace/WorkspaceContext";
import { apiFetch, json } from "@/lib/api";
import { AUTONOMY_LABELS, EVENT_LABELS, OPTION_LABELS, isLive, stageStates } from "@/lib/pipeline";
import { describeError } from "@/lib/workspace";
import type { CriteriaImpact, PipelineOverview, PipelineRunView } from "@/lib/types";

const STAGE_TABS: Record<string, string> = {
  protocol: "protocol",
  search: "search",
  screening: "screening",
  extraction: "tables",
  appraisal: "appraisal",
  synthesis: "synthesis",
  report: "report",
};
const STATE_TEXT = { done: "Done", skipped: "Skipped", current: "Current", todo: "To do" } as const;

function Pending({
  projectId,
  run,
  canWrite,
}: {
  projectId: string;
  run: PipelineRunView;
  canWrite: boolean;
}) {
  const qc = useQueryClient();
  const ws = useWorkspace();
  const { reportError } = useStatus();
  const answer = useMutation({
    mutationFn: (action: string) =>
      apiFetch(`/projects/${projectId}/pipeline/answer`, {
        method: "POST",
        ...json({ key: run.pending?.key, action }),
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["pipeline", projectId] }),
    onError: (e) => reportError(describeError(e, "Could not record your decision.")),
  });
  const p = run.pending;
  if (!p) return null;
  if (p.type !== "approval")
    return (
      <p className="rounded border border-line p-3 text-sm" role="status">
        {p.type === "wait_job"
          ? `${p.message} Progress is shown at the top right; the run continues by itself when the job ends.`
          : `${p.message} Next attempt after ${p.until ? new Date(p.until).toLocaleTimeString() : "a short wait"}.`}
      </p>
    );
  const tab = STAGE_TABS[p.stage];
  return (
    <ApprovalPrompt
      label="Pipeline decision"
      help={`Stage: ${run.stages.find((s) => s.key === p.stage)?.label ?? p.stage}`}
      testId="pipeline-approval"
      kind={p.kind}
      onAsk={ws ? () => ws.composeInChat(`About the pipeline step “${p.message}”: `) : undefined}
    >
      <p className="text-sm">{p.message}</p>
      {p.ai && <p className="mt-1 text-xs text-mark">This step sends data to the configured AI provider.</p>}
      {p.kind === "criteria_changed" && p.detail && (
        <p className="mt-1 text-xs text-muted">
          See “What the criteria change affects” below to confirm or re-decide these records.
        </p>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {tab && ws && (
          <button type="button" className="btn-secondary" onClick={() => ws.openTab(tab)}>
            Open {run.stages.find((s) => s.key === p.stage)?.label}
          </button>
        )}
        {canWrite &&
          (p.options ?? []).map((o) => (
            <button
              key={o}
              type="button"
              className={
                o === "approve" || o === "continue" ? "btn" : o === "stop" ? "btn-danger" : "btn-ghost"
              }
              disabled={answer.isPending}
              onClick={() => answer.mutate(o)}
            >
              {OPTION_LABELS[o] ?? o}
            </button>
          ))}
      </div>
    </ApprovalPrompt>
  );
}

function Impact({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const q = useQuery({
    queryKey: ["pipeline", projectId, "impact"],
    queryFn: () => apiFetch<CriteriaImpact>(`/projects/${projectId}/pipeline/impact`),
  });
  const confirm = useMutation({
    mutationFn: (includeReview: boolean) =>
      apiFetch<{ confirmed: number }>(`/projects/${projectId}/pipeline/impact/confirm`, {
        method: "POST",
        ...json({ include_needs_review: includeReview }),
      }),
    onSuccess: () => {
      for (const k of ["pipeline", "screening", "prisma", "workspace"])
        qc.invalidateQueries({ queryKey: [k, projectId] });
    },
    onError: (e) => reportError(describeError(e, "Could not confirm the decisions.")),
  });
  const c = q.data?.counts ?? {};
  const total = (c.needs_redecision ?? 0) + (c.needs_review ?? 0) + (c.carry_forward ?? 0);
  if (!q.data || total === 0) return null;
  return (
    <section
      aria-label="What the criteria change affects"
      className="rounded border border-mark/50 p-3 text-sm"
    >
      <h3 className="mb-1 font-semibold">What the criteria change affects</h3>
      <p className="text-xs text-muted">
        {total} screening decision(s) were made under an earlier protocol version. They are not carried over
        silently: confirm each group or re-decide it in Screening.
      </p>
      <ul className="mt-2 flex flex-col gap-1 text-xs">
        <li>
          <strong>{c.needs_redecision ?? 0}</strong> need a new decision (their exclusion criterion was
          removed).
        </li>
        <li>
          <strong>{c.needs_review ?? 0}</strong> need review (criterion wording changed, or criteria changed
          since inclusion).
        </li>
        <li>
          <strong>{c.carry_forward ?? 0}</strong> have an unchanged basis and can be confirmed.
        </li>
      </ul>
      {canWrite && (
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-secondary"
            disabled={!c.carry_forward || confirm.isPending}
            onClick={() => confirm.mutate(false)}
          >
            Confirm {c.carry_forward ?? 0} unchanged decision(s)
          </button>
          <button
            type="button"
            className="btn-ghost"
            disabled={!c.needs_review || confirm.isPending}
            onClick={() => confirm.mutate(true)}
            title="Only after checking each record against the new criteria"
          >
            I reviewed them: also confirm {c.needs_review ?? 0} to review
          </button>
        </div>
      )}
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-muted">Records</summary>
        <ul className="mt-1 flex flex-col gap-0.5">
          {[...q.data.needs_redecision, ...q.data.needs_review].slice(0, 50).map((d) => (
            <li key={d.paper_id}>
              {d.title || d.paper_id} — {d.decision}: {d.why}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

export default function AutopilotPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const [autonomy, setAutonomy] = useState<Record<string, boolean> | null>(null);
  const [rerunStage, setRerunStage] = useState("");
  const [rerunReason, setRerunReason] = useState("");
  const q = useQuery({
    queryKey: ["pipeline", projectId, "overview"],
    queryFn: () => apiFetch<PipelineOverview>(`/projects/${projectId}/pipeline`),
    refetchInterval: (query) => (isLive(query.state.data?.run) ? 4000 : 20_000),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["pipeline", projectId] });
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: Record<string, unknown> }) =>
      apiFetch(`/projects/${projectId}/pipeline${path}`, { method: "POST", ...json(body ?? {}) }),
    onSuccess: () => {
      setRerunReason("");
      void refresh();
      void qc.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
    onError: (e) => reportError(describeError(e, "The pipeline action failed.")),
  });
  if (!q.data) return <p className="text-muted">Loading…</p>;
  const { run, defaults, invalidations, history, limits } = q.data;
  const active = run && ["queued", "running", "waiting", "paused"].includes(run.status);
  const choice = autonomy ?? run?.autonomy ?? defaults.autonomy;
  const states = run ? stageStates(run) : null;
  return (
    <div className="flex max-w-4xl flex-col gap-5" data-testid="autopilot-panel">
      <section aria-labelledby="ap-h" className="flex flex-col gap-2">
        <h2 id="ap-h" className="text-base font-semibold">
          Review pipeline
        </h2>
        <p className="max-w-prose text-sm text-muted">
          The pipeline works through the review stage by stage. It runs routine background work you allow
          below, and always stops for your judgement: approving the protocol, screening, reviewing extracted
          values, approving appraisals, accepting the synthesis and verifying the report. Its position is
          saved after every step, so it resumes after a restart.
        </p>
        {run && (
          <p className="text-sm" role="status" data-testid="pipeline-status">
            Status: <strong>{run.status}</strong>
            {run.stage && ` · stage: ${run.stages.find((s) => s.key === run.stage)?.label ?? run.stage}`}
            {run.rerun_reason && ` · rerun because: ${run.rerun_reason}`}
            {run.error_summary && <span className="block text-danger">{run.error_summary}</span>}
          </p>
        )}
        {states && run && (
          <ol aria-label="Pipeline stages" className="flex flex-wrap gap-1 text-xs">
            {run.stages.map((s) => (
              <li
                key={s.key}
                className={`rounded border px-2 py-0.5 ${states[s.key] === "current" ? "border-mark" : "border-line"}`}
              >
                {s.label}: {STATE_TEXT[states[s.key] ?? "todo"]}
              </li>
            ))}
          </ol>
        )}
      </section>

      {run && active && <Pending projectId={projectId} run={run} canWrite={canWrite} />}
      <Impact projectId={projectId} canWrite={canWrite} />

      {canWrite && (
        <section
          aria-label="Pipeline controls"
          className="flex flex-col gap-2 rounded border border-line p-3 text-sm"
        >
          {!active && (
            <>
              <h3 className="font-semibold">Routine work the pipeline may start by itself</h3>
              {Object.keys(defaults.autonomy).map((k) => (
                <label key={k} className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={!!choice[k]}
                    onChange={(e) => setAutonomy({ ...choice, [k]: e.target.checked })}
                  />
                  {AUTONOMY_LABELS[k] ?? k}
                </label>
              ))}
              <button
                type="button"
                className="btn self-start"
                disabled={act.isPending}
                onClick={() => act.mutate({ path: "", body: { autonomy: choice } })}
              >
                {run ? "Start a new run" : "Start the pipeline"}
              </button>
            </>
          )}
          {active && run && (
            <div className="flex flex-wrap gap-2">
              {run.status === "paused" ? (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => act.mutate({ path: "/resume" })}
                >
                  Resume
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => act.mutate({ path: "/pause" })}
                >
                  Pause
                </button>
              )}
              <button type="button" className="btn-danger" onClick={() => act.mutate({ path: "/cancel" })}>
                Cancel run
              </button>
            </div>
          )}
          {run?.status === "failed" && (
            <button type="button" className="btn self-start" onClick={() => act.mutate({ path: "/recover" })}>
              Recover from the last checkpoint
            </button>
          )}
          <div className="mt-2 flex flex-wrap items-end gap-2 border-t border-line pt-2">
            <label className="flex flex-col text-2xs text-muted">
              Return to an earlier stage
              <select
                className="input !w-48"
                value={rerunStage}
                onChange={(e) => setRerunStage(e.target.value)}
              >
                <option value="">Choose a stage</option>
                {defaults.stages.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-1 flex-col text-2xs text-muted">
              Reason (kept in the history)
              <input className="input" value={rerunReason} onChange={(e) => setRerunReason(e.target.value)} />
            </label>
            <button
              type="button"
              className="btn-secondary"
              disabled={!rerunStage || !rerunReason.trim() || act.isPending}
              onClick={() => act.mutate({ path: "/rerun", body: { stage: rerunStage, reason: rerunReason } })}
            >
              Rerun from here
            </button>
          </div>
          <p className="text-2xs text-muted">
            A rerun keeps earlier runs, decisions and versions, and marks later stages for recalculation.
            Limits: {limits.max_retries} automatic retries per stage, {limits.max_jobs || "no"} job limit,{" "}
            {limits.token_budget || "no"} token budget per run, {limits.llm_max_concurrent_calls} concurrent
            AI calls.
          </p>
        </section>
      )}

      {run && Object.keys(run.changes_since_previous_run).length > 0 && (
        <section aria-label="What changed after the rerun" className="text-sm">
          <h3 className="font-semibold">What changed compared with the previous run</h3>
          <ul className="list-disc pl-5 text-xs">
            {Object.entries(run.changes_since_previous_run).map(([stage, lines]) => (
              <li key={stage}>
                {stage}: {lines.join("; ")}
              </li>
            ))}
          </ul>
        </section>
      )}

      {invalidations.length > 0 && (
        <section aria-label="Outputs needing recalculation" className="text-sm">
          <h3 className="font-semibold">Outputs flagged by upstream changes</h3>
          <ul className="flex flex-col gap-1 text-xs">
            {invalidations.map((i) => (
              <li key={i.id}>
                <strong>{i.stage}</strong> —{" "}
                {i.cause === "protocol_change" ? "protocol changed" : "earlier stage rerun"} · {i.status}
                {i.resolution && <span className="text-muted"> · {i.resolution}</span>}
                {i.status === "open" && canWrite && (
                  <button
                    type="button"
                    className="btn-ghost ml-2 text-2xs"
                    onClick={() => {
                      const reason = window.prompt("Why is this output not affected?");
                      if (reason?.trim())
                        act.mutate({
                          path: "/invalidations/dismiss",
                          body: { invalidation_id: i.id, reason },
                        });
                    }}
                  >
                    Dismiss with a reason
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {run && (
        <section aria-label="Pipeline history" className="text-sm">
          <h3 className="font-semibold">History of this run</h3>
          <ol className="flex max-h-72 flex-col gap-0.5 overflow-y-auto text-xs">
            {run.events.map((e, i) => (
              <li key={i}>
                {new Date(e.at).toLocaleString()} · {EVENT_LABELS[e.kind] ?? e.kind}
                {e.stage && ` · ${e.stage}`}
                {typeof e.payload.action === "string" &&
                  ` · ${OPTION_LABELS[e.payload.action] ?? e.payload.action}`}
                {typeof e.payload.error === "string" && (
                  <span className="text-danger"> · {e.payload.error}</span>
                )}
                {e.by_person && " · by you"}
              </li>
            ))}
          </ol>
          {history.length > 1 && (
            <p className="mt-1 text-2xs text-muted">
              Earlier runs:{" "}
              {history
                .slice(1)
                .map((h) => `${h.status} (${new Date(h.created_at).toLocaleDateString()})`)
                .join(", ")}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
