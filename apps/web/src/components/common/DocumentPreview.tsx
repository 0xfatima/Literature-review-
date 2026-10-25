"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { splitHighlight } from "@/lib/workspace";
import type { DocumentPageList } from "@/lib/types";

/** Page-by-page view of a document's extracted text (the stored text the app works with), with an optional
 *  highlighted passage. Shows OCR/extraction warnings so the user knows how reliable the text is. */
export default function DocumentPreview({
  projectId,
  documentId,
  page = 1,
  highlight,
}: {
  projectId: string;
  documentId: string;
  page?: number;
  highlight?: string | null;
}) {
  const [current, setCurrent] = useState(Math.max(1, page));
  const q = useQuery({
    queryKey: ["documents", projectId, "pages", documentId, current],
    queryFn: () =>
      apiFetch<DocumentPageList>(
        `/projects/${projectId}/documents/${documentId}/pages?offset=${current - 1}&limit=1`,
      ),
  });
  const total = q.data?.total ?? 0;
  const p = q.data?.items[0];
  const parts = p ? splitHighlight(p.text, highlight) : null;
  return (
    <div className="flex flex-col gap-2" aria-label="Document text">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button
          type="button"
          className="btn-ghost"
          disabled={current <= 1}
          onClick={() => setCurrent((c) => c - 1)}
          aria-label="Previous page"
        >
          ‹ Prev
        </button>
        <span aria-live="polite">
          Page {current}
          {total ? ` of ${total}` : ""}
        </span>
        <button
          type="button"
          className="btn-ghost"
          disabled={!total || current >= total}
          onClick={() => setCurrent((c) => c + 1)}
          aria-label="Next page"
        >
          Next ›
        </button>
        {p && p.extraction !== "text" && <span className="text-mark">Text from {p.extraction}</span>}
        {p?.ocr_confidence != null && (
          <span className="text-muted">OCR confidence {Math.round(p.ocr_confidence)}</span>
        )}
      </div>
      {q.isPending && <p className="text-xs text-muted">Loading page…</p>}
      {q.isError && <p className="text-xs text-danger">Could not load this page.</p>}
      {p && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-line bg-canvas p-2 text-xs">
          {parts ? (
            <>
              {parts[0]}
              <mark className="bg-mark/30 text-ink">{parts[1]}</mark>
              {parts[2]}
            </>
          ) : (
            p.text || "This page has no extracted text."
          )}
        </pre>
      )}
      {p && p.warnings.length > 0 && <p className="text-2xs text-mark">Warnings: {p.warnings.join("; ")}</p>}
      {q.data && q.data.total === 0 && <p className="text-xs text-muted">No extracted text yet.</p>}
    </div>
  );
}
