"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import Icon from "@/components/ui/Icon";
import { useWorkspace } from "@/components/workspace/WorkspaceContext";
import { ApiError, apiFetch, json } from "@/lib/api";
import type { Job } from "@/lib/types";

interface Chosen {
  paper_id: string;
  title: string;
  ai_decision: "include" | "exclude" | "uncertain";
  current_status: string;
  reason: string;
  reason_label: string | null;
}
interface AiFtStatus {
  awaiting: number;
  readable: number;
  without_pdf: number;
  /** Read by the last AI run but not included: they wait for the person's own decision. */
  left_for_you: number;
  consent: {
    can_send_documents: boolean;
    blocked_reason: string | null;
    notice: string;
    notice_version: string;
    consent: unknown;
    model: string | null;
  };
  active_job_id: string | null;
  last_job: {
    id: string;
    status: string;
    result: Record<string, unknown> | null;
    error: string | null;
  } | null;
  chosen: Chosen[];
}

const LABEL: Record<Chosen["ai_decision"], string> = {
  include: "Included",
  exclude: "Excluded",
  uncertain: "Uncertain",
};
const BADGE: Record<Chosen["ai_decision"], string> = {
  include: "badge-accept",
  exclude: "badge-reject",
  uncertain: "badge-uncertain",
};

/** "Let the AI read the PDFs": the AI screens every report awaiting its full-text decision that has a PDF (uploaded
 *  or fetched). When it is done the person gets one button, "View AI-chosen studies", which opens a list with each
 *  paper and an Open PDF button. */
export default function AiFullTextPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const ws = useWorkspace();
  const [ack, setAck] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ["fulltext", projectId, "ai"],
    queryFn: () => apiFetch<AiFtStatus>(`/projects/${projectId}/fulltext/ai-screen`),
    refetchInterval: (q) => (q.state.data?.active_job_id ? 2000 : 15000),
  });
  const activeId = status.data?.active_job_id ?? null;
  const job = useQuery({
    queryKey: ["jobs", projectId, activeId],
    queryFn: () => apiFetch<Job>(`/jobs/${activeId}`),
    enabled: activeId !== null,
    refetchInterval: 2000,
  });
  const wasRunning = useRef(false);
  useEffect(() => {
    if (activeId) wasRunning.current = true;
    else if (wasRunning.current) {
      wasRunning.current = false;
      for (const k of ["fulltext", "workspace", "prisma"]) qc.invalidateQueries({ queryKey: [k, projectId] });
    }
  }, [activeId, qc, projectId]);
  const start = useMutation({
    mutationFn: async () => {
      const s = status.data;
      if (s && !s.consent.consent && !s.consent.blocked_reason?.includes("server"))
        await apiFetch(`/projects/${projectId}/llm-consent`, {
          method: "POST",
          ...json({ notice_version: s.consent.notice_version }),
        });
      return apiFetch<Job>(`/projects/${projectId}/fulltext/ai-screen`, { method: "POST" });
    },
    onSuccess: () => {
      setError(null);
      for (const k of ["fulltext", "activity", "jobs"]) qc.invalidateQueries({ queryKey: [k, projectId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not start the AI."),
  });

  const s = status.data;
  if (!s || (s.awaiting === 0 && s.chosen.length === 0)) return null;
  const running = activeId !== null;
  const result = s.last_job?.result as {
    include?: number;
    exclude?: number;
    uncertain?: number;
    message?: string;
  } | null;
  const operatorBlock =
    !s.consent.can_send_documents &&
    s.consent.blocked_reason &&
    !s.consent.blocked_reason.includes("consented")
      ? s.consent.blocked_reason
      : null;

  return (
    <section
      aria-label="AI full-text screening"
      className="flex flex-col gap-2 rounded border border-mark/50 px-4 py-3"
    >
      <h3 className="flex items-center gap-2 font-semibold">
        <Icon name="sparkle" size={16} /> Let the AI read the PDFs
      </h3>
      {running ? (
        <div aria-live="polite">
          <p className="flex items-center gap-2 text-sm">
            <span className="spin inline-block h-3.5 w-3.5 rounded-full border-2 border-mark border-t-transparent" />
            {job.data?.progress_message ?? "Starting…"}
          </p>
          <progress className="mt-2 h-1.5 w-full accent-mark" max={100} value={job.data?.progress ?? 0} />
        </div>
      ) : s.chosen.length > 0 || (s.left_for_you > 0 && s.readable === 0) ? (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {s.chosen.length > 0 ? (
              <button type="button" className="btn-accept" onClick={() => setOpen(true)}>
                View AI-chosen studies ({s.chosen.length})
              </button>
            ) : (
              <span className="text-sm">The AI did not include any of the PDFs it read.</span>
            )}
            {s.readable > 0 && (
              <button
                type="button"
                className="btn-ghost"
                disabled={!canWrite || start.isPending}
                onClick={() => start.mutate()}
              >
                Read {s.readable} new PDF{s.readable === 1 ? "" : "s"}
              </button>
            )}
          </div>
          {s.left_for_you > 0 && (
            <p className="text-xs text-muted">
              {s.left_for_you} PDF{s.left_for_you === 1 ? " was" : "s were"} read but not included: decide{" "}
              {s.left_for_you === 1 ? "it" : "them"} yourself in the list below.
            </p>
          )}
        </div>
      ) : (
        <>
          <p className="text-sm">
            The AI reads the <strong>{s.readable}</strong> PDF{s.readable === 1 ? "" : "s"} available
            (uploaded or fetched) and <strong>includes</strong> the ones that meet your criteria. It never
            excludes anything: papers it does not include stay for you to decide.
            {s.without_pdf > 0 && ` ${s.without_pdf} without a PDF are also left for you.`}
          </p>
          {operatorBlock ? (
            <p className="text-sm text-danger">{operatorBlock}</p>
          ) : (
            <>
              <label className="flex items-start gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-mark"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                />
                <span>
                  OK to send the text of these PDFs to {s.consent.model ?? "the AI model"}. I can change every
                  inclusion afterwards.
                </span>
              </label>
              <div>
                <button
                  type="button"
                  className="btn-accept"
                  disabled={!canWrite || !ack || s.readable === 0 || start.isPending}
                  onClick={() => start.mutate()}
                >
                  {start.isPending
                    ? "Starting…"
                    : `Let the AI screen ${s.readable} PDF${s.readable === 1 ? "" : "s"}`}
                </button>
              </div>
            </>
          )}
        </>
      )}
      {!running && result?.message && <p className="text-sm text-mark">{result.message}</p>}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="ai-chosen-h"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={(e) => e.target === e.currentTarget && setOpen(false)}
          onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        >
          <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-lg border border-line bg-canvas shadow-xl">
            <div className="flex items-center justify-between border-b border-line px-5 py-3">
              <h2 id="ai-chosen-h" className="text-lg font-semibold">
                AI-chosen studies
              </h2>
              <button type="button" className="btn-ghost" onClick={() => setOpen(false)} autoFocus>
                Close
              </button>
            </div>
            <ul className="flex flex-col divide-y divide-line overflow-y-auto px-5">
              {[...s.chosen]
                .sort(
                  (a, b) => (a.ai_decision === "include" ? -1 : 0) - (b.ai_decision === "include" ? -1 : 0),
                )
                .map((c) => (
                  <li key={c.paper_id} className="flex items-start gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{c.title}</p>
                      <p className="mt-0.5 text-xs text-muted">
                        <span className={BADGE[c.ai_decision]}>{LABEL[c.ai_decision]}</span>{" "}
                        {c.reason_label ? `${c.reason_label} · ` : ""}
                        {c.reason}
                      </p>
                    </div>
                    {ws && (
                      <button
                        type="button"
                        className="btn-secondary shrink-0"
                        onClick={() => {
                          setOpen(false);
                          ws.openPaper(c.paper_id, "fulltext");
                        }}
                      >
                        Open PDF
                      </button>
                    )}
                  </li>
                ))}
            </ul>
            <p className="border-t border-line px-5 py-2 text-xs text-muted">
              The AI only includes. These are ordinary full-text decisions: change any of them in the list
              below.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
