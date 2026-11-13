"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useWorkspace } from "@/components/workspace/WorkspaceContext";
import { ApiError, apiFetch } from "@/lib/api";
import type { FtQueue, Job, OpenAccessSummary } from "@/lib/types";

/** Papers that passed title/abstract screening and still need a PDF: the open-access ones can be fetched
 *  automatically (arXiv, PubMed Central and other open links); the rest are listed so the person can get them. */
export default function OpenAccessBox({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const ws = useWorkspace();
  const [error, setError] = useState<string | null>(null);
  const [showList, setShowList] = useState(true);
  const summary = useQuery({
    queryKey: ["fulltext", projectId, "open-access"],
    queryFn: () => apiFetch<OpenAccessSummary>(`/projects/${projectId}/fulltext/open-access`),
    refetchInterval: 5000,
  });
  const queue = useQuery({
    queryKey: ["fulltext", projectId, "queue", "all"],
    queryFn: () => apiFetch<FtQueue>(`/projects/${projectId}/prisma/ft-queue`),
  });
  const fetchM = useMutation({
    mutationFn: () => apiFetch<Job>(`/projects/${projectId}/fulltext/fetch-open-access`, { method: "POST" }),
    onSuccess: () => {
      setError(null);
      for (const k of ["activity", "jobs", "fulltext"]) qc.invalidateQueries({ queryKey: [k, projectId] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not start fetching the PDFs."),
  });
  const s = summary.data;
  if (!s || s.awaiting === 0) return null;
  const needs = (queue.data?.items ?? []).filter(
    (i) =>
      (i.status === "awaiting" || i.status === "uncertain") &&
      i.full_text_status === "not_obtained" &&
      !i.open_access,
  );
  return (
    <section
      aria-label="PDFs still needed"
      className="flex flex-col gap-3 rounded border border-line px-4 py-3"
    >
      <p className="text-sm">
        <strong>{s.without_pdf}</strong> of the {s.awaiting} papers you included still have no PDF:{" "}
        <span className="text-ok">{s.open_access_without_pdf} open access</span>,{" "}
        <span className="text-ink">{s.needs_your_pdf} need your PDF</span>.
      </p>
      {s.fetchable > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-accept"
            disabled={!canWrite || fetchM.isPending}
            onClick={() => fetchM.mutate()}
          >
            {fetchM.isPending
              ? "Starting…"
              : `Fetch ${s.fetchable} open-access PDF${s.fetchable === 1 ? "" : "s"}`}
          </button>
          <span className="text-xs text-muted">
            Downloads from arXiv, PubMed Central and other open links. Progress shows at the top right.
          </span>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      {needs.length > 0 && (
        <div>
          <button
            type="button"
            className="text-sm font-medium underline"
            aria-expanded={showList}
            onClick={() => setShowList((v) => !v)}
          >
            Papers that need your PDF ({needs.length})
          </button>
          {showList && (
            <ul className="mt-2 flex flex-col gap-1.5 text-sm">
              {needs.map((p) => (
                <li key={p.id} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="min-w-0 flex-1">{p.title}</span>
                  {p.doi && (
                    <a
                      className="text-xs underline"
                      href={`https://doi.org/${p.doi}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Find it (DOI)
                    </a>
                  )}
                  {ws && (
                    <button
                      type="button"
                      className="text-xs underline"
                      onClick={() => ws.openPaper(p.id, "fulltext")}
                    >
                      Upload PDF
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
