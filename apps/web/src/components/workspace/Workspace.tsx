"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import { ATTENTION, STAGES, stageIndex } from "@/lib/stages";
import type { ConversationList, Job, Project } from "@/lib/types";
import { useStatus } from "@/components/status/StatusContext";
import ProtocolPanel from "@/components/protocol/ProtocolPanel";
import { useRefreshReview, useWorkflow } from "@/components/protocol/hooks";
import { WORKFLOW_STAGES, statusText } from "@/lib/protocol";
import type { WorkflowState } from "@/lib/types";

/** The review workflow's named stages, with the current one marked. Conceptual order, not a fixed sequence. */
function WorkflowTrack({ state }: { state: WorkflowState }) {
  const run = state.run;
  const current = run ? WORKFLOW_STAGES.indexOf(run.stage as (typeof WORKFLOW_STAGES)[number]) : -1;
  return (
    <ol aria-label="Workflow stages" className="flex flex-wrap gap-y-2">
      {state.stages.map((s, i) => {
        const st = i < current ? "done" : i === current ? "current" : "todo";
        return (
          <li key={s.id} aria-current={st === "current" ? "step" : undefined} className="flex items-center">
            <span
              className={`rounded px-2 py-0.5 text-xs ${
                st === "current"
                  ? "bg-mark font-semibold text-canvas"
                  : st === "done"
                    ? "text-ink"
                    : "text-muted"
              }`}
            >
              {s.label}
              {st === "done" && <span className="sr-only"> (done)</span>}
              {st === "current" && <span className="sr-only"> (current stage)</span>}
            </span>
            {i < state.stages.length - 1 && <span aria-hidden className="mx-1 h-px w-4 bg-line" />}
          </li>
        );
      })}
    </ol>
  );
}

function WorkflowSection({
  projectId,
  canWrite,
  onOpenProtocol,
}: {
  projectId: string;
  canWrite: boolean;
  onOpenProtocol: () => void;
}) {
  const wf = useWorkflow(projectId);
  const refresh = useRefreshReview(projectId);
  const { reportError } = useStatus();
  const recover = useMutation({
    mutationFn: () => apiFetch<WorkflowState>(`/projects/${projectId}/workflow/recover`, { method: "POST" }),
    onSettled: refresh,
    onError: (e) => reportError(e instanceof ApiError ? e.message : "Could not restart the workflow."),
  });
  if (!wf.data) return <p className="text-muted">Loading the workflow…</p>;
  const run = wf.data.run;
  return (
    <section aria-labelledby="wf-h">
      <h2 id="wf-h" className="section-title mb-3">
        Review workflow
      </h2>
      {run ? (
        <>
          <WorkflowTrack state={wf.data} />
          <p className="mt-3 text-sm" data-testid="workflow-status">
            {statusText(run)}
          </p>
          {(run.status === "failed" || run.status === "needs_attention") && (
            <button
              type="button"
              className="btn-secondary mt-2"
              disabled={!canWrite || recover.isPending}
              onClick={() => recover.mutate()}
            >
              Restart the workflow from the saved protocol
            </button>
          )}
        </>
      ) : (
        <p className="max-w-prose text-muted">The review protocol has not been started yet.</p>
      )}
      <button type="button" className="btn-secondary mt-3" onClick={onOpenProtocol}>
        {run ? "Open the protocol" : "Set up the protocol"}
      </button>
    </section>
  );
}

export const TABS = [
  { id: "status", label: "Review status" },
  { id: "protocol", label: "Protocol" },
  { id: "papers", label: "Papers" },
  { id: "tables", label: "Tables" },
  { id: "artifacts", label: "Artifacts" },
] as const;
export type TabId = (typeof TABS)[number]["id"];

function StageTrack({ status }: { status: string }) {
  const current = stageIndex(status);
  return (
    <div>
      <ol aria-label="Review stages" className="flex flex-wrap gap-y-2">
        {STAGES.map((s, i) => {
          const state = i < current ? "done" : i === current ? "current" : "todo";
          return (
            <li
              key={s.id}
              aria-current={state === "current" ? "step" : undefined}
              className="flex items-center"
            >
              <span
                className={`rounded px-2 py-0.5 text-xs ${
                  state === "current"
                    ? "bg-mark font-semibold text-canvas"
                    : state === "done"
                      ? "text-ink"
                      : "text-muted"
                }`}
              >
                {s.label}
                {state === "done" && <span className="sr-only"> (done)</span>}
                {state === "current" && <span className="sr-only"> (current stage)</span>}
              </span>
              {i < STAGES.length - 1 && <span aria-hidden className="mx-1 h-px w-4 bg-line" />}
            </li>
          );
        })}
      </ol>
      {status === ATTENTION.id && (
        <p className="mt-2 text-sm text-danger">This project needs attention before it can continue.</p>
      )}
    </div>
  );
}

function Empty({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="max-w-prose py-10">
      <h2 className="mb-2 text-base font-semibold">{title}</h2>
      <div className="text-muted">{children}</div>
    </div>
  );
}

/** Main workspace. Tabs without a backend yet say so; they never show sample or invented records. */
export default function Workspace({
  project,
  conversations,
  tab,
  onTab,
  onOpenConversation,
}: {
  project: Project;
  conversations: ConversationList | undefined;
  tab: TabId;
  onTab: (t: TabId) => void;
  onOpenConversation: (conversationId: string) => void;
}) {
  const qc = useQueryClient();
  const { reportError } = useStatus();
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const messageTotal = conversations?.items.reduce((n, c) => n + c.message_count, 0) ?? 0;

  const workerCheck = useMutation({
    mutationFn: () =>
      apiFetch<Job>("/jobs/ping", { method: "POST", ...json({ project_id: project.id, delay_s: 3 }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", project.id] }),
    onError: (e) =>
      reportError(
        e instanceof ApiError && e.code === "job_already_active"
          ? "A worker check is already running."
          : e instanceof ApiError
            ? e.message
            : "Could not start the worker check.",
      ),
  });

  return (
    <div className="flex min-h-full flex-col">
      <div
        role="tablist"
        aria-label="Workspace"
        className="flex shrink-0 gap-1 border-b border-line px-3 pt-2"
      >
        {TABS.map((t, i) => {
          const selected = t.id === tab;
          return (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el;
              }}
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              className={`-mb-px rounded-t border-b-2 px-3 py-1.5 text-sm ${
                selected ? "border-mark text-ink" : "border-transparent text-muted hover:text-ink"
              }`}
              onClick={() => onTab(t.id)}
              onKeyDown={(e) => {
                const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!step) return;
                e.preventDefault();
                const next = TABS[(i + step + TABS.length) % TABS.length]!;
                onTab(next.id);
                tabRefs.current[next.id]?.focus();
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="flex-1 px-6 py-5">
        {tab === "status" && (
          <div className="flex max-w-3xl flex-col gap-8">
            <section aria-labelledby="stage-h">
              <h2 id="stage-h" className="section-title mb-3">
                Where this review is
              </h2>
              <StageTrack status={project.status} />
            </section>

            <section aria-labelledby="topic-h">
              <h2 id="topic-h" className="section-title mb-2">
                Original topic
              </h2>
              <p className="max-w-prose whitespace-pre-wrap">{project.topic_original}</p>
            </section>

            <section aria-labelledby="facts-h">
              <h2 id="facts-h" className="sr-only">
                Project details
              </h2>
              <dl className="grid grid-cols-[9rem_1fr] gap-y-2 text-sm">
                <dt className="text-muted">Created</dt>
                <dd>{new Date(project.created_at).toLocaleString()}</dd>
                <dt className="text-muted">Last updated</dt>
                <dd>{new Date(project.updated_at).toLocaleString()}</dd>
                <dt className="text-muted">Chat threads</dt>
                <dd>{conversations ? conversations.total : "…"}</dd>
                <dt className="text-muted">Messages</dt>
                <dd>{conversations ? messageTotal : "…"}</dd>
              </dl>
            </section>

            <WorkflowSection
              projectId={project.id}
              canWrite={project.archived_at === null}
              onOpenProtocol={() => onTab("protocol")}
            />

            <section aria-labelledby="sys-h">
              <h2 id="sys-h" className="section-title mb-2">
                Background processing
              </h2>
              <p className="mb-2 max-w-prose text-sm text-muted">
                Long tasks such as searches and PDF parsing will run in the background and report progress in
                the status bar. You can check that the background worker is responding now.
              </p>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => workerCheck.mutate()}
                disabled={workerCheck.isPending}
              >
                Check background worker
              </button>
            </section>
          </div>
        )}
        {tab === "protocol" && (
          <ProtocolPanel
            projectId={project.id}
            canWrite={project.archived_at === null}
            onOpenConversation={onOpenConversation}
          />
        )}
        {tab === "papers" && (
          <Empty title="No papers yet">
            Papers appear here once the approved protocol has been searched in scholarly databases. Every
            record will show where it came from.
          </Empty>
        )}
        {tab === "tables" && (
          <Empty title="No tables yet">
            The evidence matrix and comparison tables are built from data extracted from included papers, each
            value linked to its page and passage.
          </Empty>
        )}
        {tab === "artifacts" && (
          <Empty title="No artifacts yet">
            PRISMA diagrams, synthesis versions and draft exports are saved here as they are produced.
          </Empty>
        )}
      </div>
    </div>
  );
}
