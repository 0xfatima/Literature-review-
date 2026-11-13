"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import type { ScreeningProgress } from "@/lib/types";

/** Screening by hand without deciding every record: once the person has included the papers they want, the rest
 *  can be excluded in one step (reason "Other": not selected during manual screening). Every one of those decisions
 *  is recorded, shares one batch (one Undo) and can be changed later, so the review stays traceable. */
export const MANUAL_NOTE = "Not selected during manual screening (excluded in one step).";

export default function FinishScreening({
  projectId,
  progress,
  canWrite,
  onDone,
}: {
  projectId: string;
  progress: ScreeningProgress;
  canWrite: boolean;
  onDone?: () => void;
}) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [undo, setUndo] = useState<string | null>(null);
  const refresh = () => {
    for (const k of ["screening", "relevance", "workspace", "prisma", "workflow", "project"])
      qc.invalidateQueries({ queryKey: [k, projectId] });
  };
  const finish = useMutation({
    mutationFn: () =>
      apiFetch<{ batch_id: string | null; changed: number }>(`/projects/${projectId}/screening/bulk`, {
        method: "POST",
        ...json({
          decision: "exclude",
          reason_code: "other",
          note: MANUAL_NOTE,
          all_matching: { decision: "undecided" },
        }),
      }),
    onSuccess: (r) => {
      setConfirming(false);
      setError(null);
      setUndo(r.batch_id);
      refresh();
      onDone?.();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not finish screening."),
  });
  const undoM = useMutation({
    mutationFn: (batchId: string) =>
      apiFetch(`/projects/${projectId}/screening/undo`, { method: "POST", ...json({ batch_id: batchId }) }),
    onSuccess: () => {
      setUndo(null);
      refresh();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not undo."),
  });

  if (undo)
    return (
      <p className="flex flex-wrap items-center gap-2 text-sm" role="status">
        <span className="badge-accept">✓ Screening finished</span>
        The undecided records were excluded.
        <button
          type="button"
          className="btn-ghost"
          disabled={undoM.isPending}
          onClick={() => undoM.mutate(undo)}
        >
          Undo
        </button>
      </p>
    );
  if (progress.undecided === 0) return null;
  const decided = progress.include + progress.exclude + progress.uncertain;
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>
        Picked the papers you want? You don&apos;t have to open every record: exclude the{" "}
        <strong>{progress.undecided}</strong> you haven&apos;t decided in one step and move on.
        {progress.include === 0 && " (You haven't included any papers yet.)"}
      </p>
      {progress.uncertain > 0 && (
        <p className="text-xs text-muted">
          {progress.uncertain} record{progress.uncertain === 1 ? " is" : "s are"} marked uncertain and stay as
          they are: include or exclude {progress.uncertain === 1 ? "it" : "them"} to complete the step.
        </p>
      )}
      {confirming ? (
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Confirm finishing screening"
        >
          <span>
            Exclude {progress.undecided} record{progress.undecided === 1 ? "" : "s"} and keep your {decided}{" "}
            decision
            {decided === 1 ? "" : "s"}?
          </span>
          <button
            type="button"
            className="btn-reject"
            disabled={!canWrite || finish.isPending}
            onClick={() => finish.mutate()}
          >
            {finish.isPending ? "Excluding…" : `Yes, exclude ${progress.undecided}`}
          </button>
          <button type="button" className="btn-ghost" onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div>
          <button
            type="button"
            className="btn-reject-soft"
            disabled={!canWrite || decided === 0}
            title={decided === 0 ? "Decide at least one record first" : undefined}
            onClick={() => setConfirming(true)}
          >
            Finish screening: exclude the {progress.undecided} undecided
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
