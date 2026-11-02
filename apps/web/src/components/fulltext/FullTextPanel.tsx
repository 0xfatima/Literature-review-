"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import {
  citeLabel,
  decisionProblem,
  FT_STATUS_LABELS,
  FT_STATUS_ORDER,
  FULL_TEXT_LABELS,
  groupReasons,
  type FtDecision,
} from "@/lib/fulltext";
import type { FtQueue, FtQueueItem, FtReason, FtStatus } from "@/lib/types";
import { useWorkspace } from "@/components/workspace/WorkspaceContext";
import StudiesPanel from "@/components/fulltext/StudiesPanel";

const STATUS_TONE: Record<FtStatus, string> = {
  awaiting: "text-mark",
  uncertain: "text-mark",
  included: "text-ok",
  excluded: "text-danger",
  not_retrieved: "text-muted",
};

/** Keys that change when a full-text outcome is recorded. */
const AFFECTED = ["fulltext", "prisma", "studies", "workspace", "extraction", "appraisal", "synthesis"];

function useRefresh(projectId: string) {
  const qc = useQueryClient();
  return () => AFFECTED.forEach((k) => qc.invalidateQueries({ queryKey: [k, projectId] }));
}

type Mode = { kind: "exclude" } | { kind: "not_retrieved" } | null;

function QueueItem({
  projectId,
  item,
  reasons,
  canWrite,
}: {
  projectId: string;
  item: FtQueueItem;
  reasons: FtReason[];
  canWrite: boolean;
}) {
  const ws = useWorkspace();
  const refresh = useRefresh(projectId);
  const [mode, setMode] = useState<Mode>(null);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const done = () => {
    setMode(null);
    setReason("");
    setNote("");
    setError(null);
    refresh();
  };
  const fail = (fallback: string) => (e: unknown) =>
    setError(
      e instanceof ApiError && e.code === "version_conflict"
        ? "This record changed in another tab. The list has been reloaded; try again."
        : e instanceof ApiError
          ? e.message
          : fallback,
    );

  const decide = useMutation({
    mutationFn: (body: { decision: FtDecision; reason_code?: string; note?: string }) =>
      apiFetch(`/projects/${projectId}/prisma/ft-decisions/${item.id}`, {
        method: "POST",
        ...json({ ...body, expected_row_version: item.row_version }),
      }),
    onSuccess: done,
    onError: (e) => {
      fail("Could not record the decision.")(e);
      if (e instanceof ApiError && e.code === "version_conflict") refresh();
    },
  });
  const retrieval = useMutation({
    mutationFn: (body: { retrieved: boolean; note?: string }) =>
      apiFetch(`/projects/${projectId}/prisma/retrieval/${item.id}`, { method: "POST", ...json(body) }),
    onSuccess: done,
    onError: fail("Could not update the retrieval status."),
  });
  const busy = decide.isPending || retrieval.isPending;
  const problem = mode?.kind === "exclude" ? decisionProblem("exclude", reason, note) : null;

  return (
    <li className="rounded border border-line bg-panel p-3" aria-labelledby={`ft-title-${item.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p id={`ft-title-${item.id}`} className="font-medium">
            {item.title}
          </p>
          <p className="text-xs text-muted">
            {citeLabel(item)}
            {item.venue ? ` · ${item.venue}` : ""}
            {item.doi ? ` · doi:${item.doi}` : ""}
          </p>
          <p className="mt-1 text-xs">
            <span className={`font-medium ${STATUS_TONE[item.status]}`}>{FT_STATUS_LABELS[item.status]}</span>
            <span className="text-muted"> · {FULL_TEXT_LABELS[item.full_text_status]}</span>
          </p>
          {item.reason_label && <p className="mt-1 text-xs">Reason: {item.reason_label}</p>}
          {item.note && <p className="mt-0.5 text-xs text-muted">Note: {item.note}</p>}
        </div>
        {ws && (
          <button type="button" className="btn-ghost" onClick={() => ws.openPaper(item.id, "fulltext")}>
            {item.full_text_status === "parsed" ? "Read the PDF" : "Open paper"}
          </button>
        )}
      </div>

      {canWrite && mode === null && (
        <div
          className="mt-2 flex flex-wrap gap-2"
          role="group"
          aria-label={`Full-text decision for ${item.title}`}
        >
          {item.status === "not_retrieved" ? (
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => retrieval.mutate({ retrieved: true })}
            >
              Full text found after all
            </button>
          ) : (
            <>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                aria-pressed={item.status === "included"}
                onClick={() => decide.mutate({ decision: "include" })}
              >
                Include
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                aria-pressed={item.status === "uncertain"}
                onClick={() => decide.mutate({ decision: "uncertain" })}
              >
                Uncertain
              </button>
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                aria-pressed={item.status === "excluded"}
                onClick={() => {
                  setMode({ kind: "exclude" });
                  setReason(item.reason_code ?? "");
                  setNote(item.note ?? "");
                }}
              >
                Exclude…
              </button>
              {(item.status === "awaiting" || item.status === "uncertain") && (
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => setMode({ kind: "not_retrieved" })}
                >
                  Could not get the full text…
                </button>
              )}
            </>
          )}
        </div>
      )}

      {canWrite && mode?.kind === "exclude" && (
        <div className="mt-2 flex flex-col gap-2 rounded border border-line bg-canvas p-3">
          <label className="text-xs text-muted" htmlFor={`ft-reason-${item.id}`}>
            Reason for excluding this report (required)
          </label>
          <select
            id={`ft-reason-${item.id}`}
            className="input"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          >
            <option value="">Choose a reason</option>
            {groupReasons(reasons).map((g) => (
              <optgroup key={g.label} label={g.label}>
                {g.options.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <label className="text-xs text-muted" htmlFor={`ft-note-${item.id}`}>
            Note {reason === "other" ? "(required for “Other”)" : "(optional)"}
          </label>
          <textarea
            id={`ft-note-${item.id}`}
            className="input"
            rows={2}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              className="btn"
              disabled={busy || problem !== null}
              onClick={() =>
                decide.mutate({ decision: "exclude", reason_code: reason, note: note.trim() || undefined })
              }
            >
              Exclude
            </button>
            <button type="button" className="btn-ghost" onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
          {problem && <p className="text-xs text-muted">{problem}</p>}
        </div>
      )}

      {canWrite && mode?.kind === "not_retrieved" && (
        <div className="mt-2 flex flex-col gap-2 rounded border border-line bg-canvas p-3">
          <p className="text-xs text-muted">
            Use this only when you tried to obtain the full text and could not (for example no library access
            and an interlibrary loan was refused). PRISMA reports it as “reports not retrieved”. You can undo
            it later.
          </p>
          <label className="text-xs text-muted" htmlFor={`ft-nr-${item.id}`}>
            What did you try? (optional)
          </label>
          <textarea
            id={`ft-nr-${item.id}`}
            className="input"
            rows={2}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => retrieval.mutate({ retrieved: false, note: note.trim() || undefined })}
            >
              Mark as not retrieved
            </button>
            <button type="button" className="btn-ghost" onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs text-danger" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

export default function FullTextPanel({
  projectId,
  canWrite,
  onOpenScreening,
  onOpenFiles,
}: {
  projectId: string;
  canWrite: boolean;
  onOpenScreening: () => void;
  onOpenFiles: () => void;
}) {
  const [status, setStatus] = useState<FtStatus | "all">("all");
  const queue = useQuery({
    queryKey: ["fulltext", projectId, "queue", status],
    queryFn: () =>
      apiFetch<FtQueue>(
        `/projects/${projectId}/prisma/ft-queue${status === "all" ? "" : `?status=${status}`}`,
      ),
  });
  const reasons = useQuery({
    queryKey: ["fulltext", projectId, "reasons"],
    queryFn: () => apiFetch<FtReason[]>(`/projects/${projectId}/prisma/ft-reasons`),
    retry: false,
  });

  const counts = queue.data?.counts;
  const total = counts ? FT_STATUS_ORDER.reduce((n, s) => n + counts[s], 0) : 0;

  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <section aria-labelledby="ft-h" className="flex flex-col gap-3">
        <div>
          <h2 id="ft-h" className="section-title mb-1">
            Full-text screening
          </h2>
          <p className="text-xs text-muted">
            Records you included at title and abstract need a closer look using the whole paper. Read the PDF,
            then include or exclude each report against your approved criteria. Every exclusion here needs a
            reason, and every decision is kept in the history and counted in the PRISMA flow. Upload PDFs in
            the{" "}
            <button type="button" className="underline" onClick={onOpenFiles}>
              Files
            </button>{" "}
            tab or from the paper itself.
          </p>
        </div>

        {queue.isLoading && <p className="text-muted">Loading full-text screening…</p>}
        {queue.isError && <p className="text-danger">Could not load the full-text screening list.</p>}
        {reasons.isError && (
          <p className="text-xs text-danger">
            Exclusion reasons could not be loaded
            {reasons.error instanceof ApiError ? `: ${reasons.error.message}` : "."}
          </p>
        )}

        {counts && total === 0 && status === "all" && (
          <div className="rounded border border-line bg-panel p-4 text-sm">
            <p>No records have passed title and abstract screening yet.</p>
            <button type="button" className="btn-secondary mt-2" onClick={onOpenScreening}>
              Go to screening
            </button>
          </div>
        )}

        {counts && total > 0 && (
          <div className="flex flex-wrap gap-2" role="group" aria-label="Show records by full-text status">
            <button
              type="button"
              className={status === "all" ? "btn" : "btn-secondary"}
              aria-pressed={status === "all"}
              onClick={() => setStatus("all")}
            >
              All ({total})
            </button>
            {FT_STATUS_ORDER.map((s) => (
              <button
                key={s}
                type="button"
                className={status === s ? "btn" : "btn-secondary"}
                aria-pressed={status === s}
                onClick={() => setStatus(s)}
              >
                {FT_STATUS_LABELS[s]} ({counts[s]})
              </button>
            ))}
          </div>
        )}

        {queue.data && queue.data.items.length === 0 && total > 0 && (
          <p className="text-sm text-muted">No records with this status.</p>
        )}
        {queue.data && queue.data.items.length > 0 && (
          <ul className="flex flex-col gap-2">
            {queue.data.items.map((item) => (
              <QueueItem
                key={item.id}
                projectId={projectId}
                item={item}
                reasons={reasons.data ?? []}
                canWrite={canWrite && reasons.isSuccess}
              />
            ))}
          </ul>
        )}
      </section>

      <StudiesPanel projectId={projectId} canWrite={canWrite} />
    </div>
  );
}
