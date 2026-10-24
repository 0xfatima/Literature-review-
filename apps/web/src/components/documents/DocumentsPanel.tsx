"use client";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { ApiError, apiFetch, json } from "@/lib/api";
import {
  EXTRACTION_LABELS,
  MATCH_LABELS,
  PARSE_LABELS,
  coverageText,
  formatBytes,
  isActiveDoc,
  usageText,
  warningTitle,
} from "@/lib/documents";
import { fmtDate } from "@/lib/search";
import type { DocumentList, DocumentPageList, DocumentRecord, DocumentSummary, PaperList } from "@/lib/types";
import { useStatus } from "@/components/status/StatusContext";
import { UploadQueue, useUploader } from "./Uploader";

const msg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function useDocumentSummary(projectId: string) {
  return useQuery({
    queryKey: ["documents", projectId, "summary"],
    queryFn: () => apiFetch<DocumentSummary>(`/projects/${projectId}/documents/summary`),
  });
}

export function useDocuments(projectId: string, params: URLSearchParams) {
  return useQuery({
    queryKey: ["documents", projectId, "list", params.toString()],
    queryFn: () => apiFetch<DocumentList>(`/projects/${projectId}/documents?${params}`),
    placeholderData: keepPreviousData,
    // reading runs in the background: keep looking while anything is still queued or being read
    refetchInterval: (q) => (q.state.data?.items.some(isActiveDoc) ? 1500 : false),
  });
}

export function useRefreshDocuments(projectId: string) {
  const qc = useQueryClient();
  return useCallback(() => {
    for (const k of ["documents", "papers", "jobs", "project"])
      qc.invalidateQueries({ queryKey: [k, projectId] });
  }, [qc, projectId]);
}

/* ---------- link a document to a paper ---------- */

function PaperPicker({
  projectId,
  onPick,
  disabled,
}: {
  projectId: string;
  onPick: (paperId: string) => void;
  disabled: boolean;
}) {
  const [q, setQ] = useState("");
  const found = useQuery({
    queryKey: ["papers", projectId, "picker", q],
    queryFn: () =>
      apiFetch<PaperList>(`/projects/${projectId}/papers?q=${encodeURIComponent(q)}&limit=5&sort=title`),
    enabled: q.trim().length >= 2,
  });
  return (
    <div className="mt-2">
      <label className="text-xs text-muted" htmlFor={`pick-${projectId}`}>
        Search your papers by title to link this file
      </label>
      <input
        id={`pick-${projectId}`}
        className="input mt-1"
        value={q}
        maxLength={200}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Type at least 2 letters"
      />
      {found.data && found.data.items.length === 0 && (
        <p className="mt-1 text-xs text-muted">No paper found.</p>
      )}
      <ul className="mt-1 flex flex-col gap-1">
        {found.data?.items.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate">
              {p.title} {p.year && <span className="text-xs text-muted">({p.year})</span>}
            </span>
            <button type="button" className="btn-secondary" disabled={disabled} onClick={() => onPick(p.id)}>
              Link
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------- extracted text, page by page ---------- */

function PageViewer({ projectId, doc }: { projectId: string; doc: DocumentRecord }) {
  const [page, setPage] = useState(1);
  const [jump, setJump] = useState("");
  const total = doc.page_count ?? 0;
  const data = useQuery({
    queryKey: ["documents", projectId, "pages", doc.id, doc.text_version, page],
    queryFn: () =>
      apiFetch<DocumentPageList>(
        `/projects/${projectId}/documents/${doc.id}/pages?offset=${page - 1}&limit=1`,
      ),
    placeholderData: keepPreviousData,
  });
  const p = data.data?.items[0];
  return (
    <div className="mt-2 rounded border border-line bg-canvas p-3" data-testid="page-viewer">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button
          type="button"
          className="btn-secondary"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          Previous page
        </button>
        <span aria-live="polite">
          Page {page} of {total}
        </span>
        <button
          type="button"
          className="btn-secondary"
          disabled={page >= total}
          onClick={() => setPage(page + 1)}
        >
          Next page
        </button>
        <label className="ml-2 text-xs text-muted" htmlFor={`jump-${doc.id}`}>
          Go to page
        </label>
        <input
          id={`jump-${doc.id}`}
          className="input !w-20"
          inputMode="numeric"
          value={jump}
          onChange={(e) => setJump(e.target.value.replace(/\D/g, ""))}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            const n = Number(jump);
            if (n >= 1 && n <= total) setPage(n);
          }}
        />
      </div>
      {data.isError && <p className="mt-2 text-danger">Could not load this page.</p>}
      {p && (
        <>
          <p className="mt-2 text-xs text-muted">
            {EXTRACTION_LABELS[p.extraction]}
            {p.extraction === "ocr" &&
              p.ocr_confidence !== null &&
              ` · confidence ${Math.round(p.ocr_confidence)}%`}
            {p.extraction === "ocr" && " · may contain recognition errors"}
          </p>
          {p.extraction === "unreadable" && (
            <p className="mt-1 text-sm text-mark">
              This page could not be read, so no text is shown. Nothing has been guessed.
            </p>
          )}
          {p.extraction === "blank" && <p className="mt-1 text-sm text-muted">This page has no text.</p>}
          {p.text && (
            <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words text-sm" tabIndex={0}>
              {p.text}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

/* ---------- one document ---------- */

function StatusChip({ doc }: { doc: DocumentRecord }) {
  const tone =
    doc.parse_status === "parsed" ? "text-ok" : doc.parse_status === "failed" ? "text-danger" : "text-mark";
  return (
    <span className={`text-xs font-medium ${tone}`} data-testid="doc-status">
      {PARSE_LABELS[doc.parse_status]}
    </span>
  );
}

function DocumentCard({
  projectId,
  doc,
  canWrite,
}: {
  projectId: string;
  doc: DocumentRecord;
  canWrite: boolean;
}) {
  const refresh = useRefreshDocuments(projectId);
  const { reportError } = useStatus();
  const [showText, setShowText] = useState(false);
  const [picking, setPicking] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const base = `/projects/${projectId}/documents/${doc.id}`;
  const act = useMutation({
    mutationFn: (a: { path: string; method?: string; body?: unknown }) =>
      apiFetch<unknown>(`${base}${a.path}`, { method: a.method ?? "POST", ...(a.body ? json(a.body) : {}) }),
    onSettled: refresh,
    onError: (e) => reportError(msg(e, "That did not work. Please try again.")),
  });
  const link = (paperId: string | null) =>
    act.mutate({ path: "/link", body: { paper_id: paperId, expected_version: doc.row_version } });
  const busy = act.isPending || !canWrite;
  const coverage = coverageText(doc);

  return (
    <li className="rounded border border-line bg-panel px-4 py-3" data-testid="document-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="break-all text-sm font-medium">{doc.original_filename}</h3>
        <StatusChip doc={doc} />
      </div>
      <p className="mt-0.5 text-xs text-muted">
        {formatBytes(doc.size_bytes)} · uploaded {fmtDate(doc.created_at)}
        {doc.page_count !== null && ` · ${doc.page_count} page${doc.page_count === 1 ? "" : "s"}`}
        {doc.scan_status === "not_scanned" && " · not scanned for malware"}
        {doc.scan_status === "clean" && " · malware scan clean"}
      </p>
      {coverage && <p className="mt-1 text-xs">{coverage}</p>}

      {doc.parse_status === "failed" && (
        <p className="mt-2 text-sm text-danger" role="alert">
          {doc.error_message ?? "This file could not be read."}
        </p>
      )}

      {doc.warnings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1" aria-label="Notes about this file">
          {doc.warnings.map((w, i) => (
            <li key={`${w.code}-${i}`} className="text-xs text-mark">
              <strong>{warningTitle(w)}.</strong> {w.message}
              {w.pages &&
                w.pages.length > 0 &&
                ` Pages: ${w.pages.slice(0, 20).join(", ")}${w.pages.length > 20 ? "…" : ""}.`}
            </li>
          ))}
        </ul>
      )}

      {/* what paper is this? */}
      <div className="mt-2 border-t border-line pt-2 text-sm" data-testid="match-section">
        {isActiveDoc(doc) ? (
          <p className="text-muted">It will be matched to a paper once it has been read.</p>
        ) : doc.needs_confirmation ? (
          <div>
            <p className="font-medium">{MATCH_LABELS.needs_confirmation}: this file resembles</p>
            <ul className="mt-1 flex flex-col gap-1.5">
              {doc.candidates.map((c) => (
                <li key={c.paper_id} className="rounded border border-line px-2 py-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <span>
                      {c.title ?? "(paper)"}{" "}
                      {c.year && <span className="text-xs text-muted">({c.year})</span>}
                      <span className="block text-xs text-muted">
                        {c.reasons.join("; ")}
                        {c.score !== null && ` · similarity ${Math.round(c.score * 100)}%`}
                      </span>
                    </span>
                    <button type="button" className="btn" disabled={busy} onClick={() => link(c.paper_id)}>
                      Confirm
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="btn-ghost mt-1"
              disabled={busy}
              onClick={() => setPicking(!picking)}
            >
              None of these: choose another paper
            </button>
          </div>
        ) : doc.paper_id ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p>
              <span className="font-medium">{MATCH_LABELS[doc.match_status]}:</span> {doc.paper_title}
              {doc.match_status === "auto_matched" && doc.match_method && (
                <span className="text-xs text-muted">
                  {" "}
                  (by {doc.match_method.replace("_", " + ")}; check it)
                </span>
              )}
            </p>
            <div className="flex gap-1">
              {doc.match_status === "auto_matched" && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy}
                  onClick={() => link(doc.paper_id)}
                >
                  Confirm
                </button>
              )}
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => link(null)}>
                {doc.match_status === "auto_matched" ? "Not this paper" : "Unlink"}
              </button>
            </div>
          </div>
        ) : (
          <div>
            <p className="text-muted">
              {MATCH_LABELS.unmatched}. Link it so its text can be used for this paper.
            </p>
            <button
              type="button"
              className="btn-secondary mt-1"
              disabled={busy}
              onClick={() => setPicking(!picking)}
            >
              Choose a paper
            </button>
          </div>
        )}
        {picking && !isActiveDoc(doc) && (
          <PaperPicker
            projectId={projectId}
            disabled={busy}
            onPick={(id) => {
              setPicking(false);
              link(id);
            }}
          />
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        {doc.parse_status === "parsed" && (
          <button
            type="button"
            className="btn-secondary"
            aria-expanded={showText}
            onClick={() => setShowText(!showText)}
          >
            {showText ? "Hide extracted text" : "View extracted text"}
          </button>
        )}
        {doc.can_retry && (
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={() => act.mutate({ path: "/retry" })}
          >
            {doc.parse_status === "failed" ? "Try reading again" : "Read again (OCR is now on)"}
          </button>
        )}
        {doc.can_download && (
          <a className="btn-secondary no-underline" href={`/api/v1${base}/download`} download>
            Download original
          </a>
        )}
        {!confirmRemove ? (
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => setConfirmRemove(true)}>
            Remove
          </button>
        ) : (
          <span className="flex items-center gap-2 text-sm">
            Remove this file from the project?
            <button
              type="button"
              className="btn-danger"
              disabled={busy}
              onClick={() => act.mutate({ path: "", method: "DELETE" })}
            >
              Yes, remove
            </button>
            <button type="button" className="btn-ghost" onClick={() => setConfirmRemove(false)}>
              Keep
            </button>
          </span>
        )}
      </div>
      {showText && doc.parse_status === "parsed" && <PageViewer projectId={projectId} doc={doc} />}
    </li>
  );
}

/* ---------- the Files tab ---------- */

export default function DocumentsPanel({ projectId, canWrite }: { projectId: string; canWrite: boolean }) {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [drag, setDrag] = useState(false);
  const refresh = useRefreshDocuments(projectId);
  const summary = useDocumentSummary(projectId);
  const maxBytes = summary.data?.limits.max_bytes ?? 50 * 1024 * 1024;
  const uploader = useUploader(projectId, null, maxBytes, refresh);
  const PAGE = 25;
  const params = new URLSearchParams({ offset: String(offset), limit: String(PAGE) });
  if (status) params.set("status", status);
  if (q.trim()) params.set("q", q.trim());
  const docs = useDocuments(projectId, params);
  const lim = summary.data?.limits;

  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <section aria-labelledby="files-h">
        <h2 id="files-h" className="section-title mb-2">
          Full-text PDFs
        </h2>
        <p className="max-w-prose text-sm text-muted">
          Upload papers you are authorised to access (for example through your institution or from an
          open-access source). This application never downloads paywalled papers for you and never treats a
          paper as read until a file has been uploaded and its text extracted.
        </p>

        <label
          className={`mt-3 flex cursor-pointer flex-col items-center gap-1 rounded border-2 border-dashed px-4 py-8 text-center text-sm focus-within:outline focus-within:outline-2 focus-within:outline-mark ${
            drag ? "border-mark bg-raised" : "border-line"
          } ${canWrite ? "" : "pointer-events-none opacity-60"}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (canWrite && e.dataTransfer.files.length) uploader.add(e.dataTransfer.files);
          }}
        >
          <span className="font-medium">Drop PDF files here, or choose files</span>
          <span className="text-xs text-muted">
            PDF only{lim ? `, up to ${formatBytes(lim.max_bytes)} each` : ""}
          </span>
          <input
            type="file"
            multiple
            accept=".pdf,application/pdf"
            className="sr-only"
            disabled={!canWrite}
            data-testid="file-input"
            onChange={(e) => {
              if (e.target.files?.length) uploader.add(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
        {!canWrite && (
          <p className="mt-1 text-xs text-muted">This project is archived: uploads are disabled.</p>
        )}
        {lim && summary.data && (
          <p className="mt-2 text-xs text-muted" data-testid="usage">
            {usageText(
              summary.data.usage.project_bytes,
              lim.quota_bytes_per_project,
              summary.data.usage.project_files,
              lim.quota_files_per_project,
            )}
            {" · "}
            {lim.ocr_enabled
              ? `Scanned pages are read by OCR (${lim.ocr_languages}).`
              : "OCR is off: scanned (image-only) pages cannot be read."}
            {" · "}
            {lim.malware_scanner === "none"
              ? "Files are not scanned for malware."
              : "Files are scanned for malware."}
          </p>
        )}
      </section>

      <UploadQueue items={uploader.items} onCancel={uploader.cancel} onDismiss={uploader.dismiss} />

      <section aria-labelledby="docs-h">
        <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
          <h2 id="docs-h" className="section-title">
            Uploaded files {docs.data && <span className="font-normal">({docs.data.total})</span>}
          </h2>
          <div className="flex flex-wrap gap-2">
            <label className="sr-only" htmlFor="doc-q">
              Search file names
            </label>
            <input
              id="doc-q"
              className="input !w-44"
              placeholder="Search file names"
              value={q}
              maxLength={200}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
            />
            <label className="sr-only" htmlFor="doc-status">
              Filter by status
            </label>
            <select
              id="doc-status"
              className="input !w-44"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Any status</option>
              {Object.entries(PARSE_LABELS).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
        {docs.isError && <p className="text-danger">Could not load the uploaded files.</p>}
        {docs.data?.items.length === 0 && (
          <p className="text-muted">{status || q ? "No file matches." : "No files uploaded yet."}</p>
        )}
        <ul className="flex flex-col gap-2" aria-live="polite">
          {docs.data?.items.map((d) => (
            <DocumentCard key={d.id} projectId={projectId} doc={d} canWrite={canWrite} />
          ))}
        </ul>
        {docs.data && docs.data.total > PAGE && (
          <nav aria-label="Pages" className="mt-3 flex items-center gap-3 text-sm">
            <button
              type="button"
              className="btn-secondary"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              Previous
            </button>
            <span className="text-muted">
              {offset + 1}–{Math.min(offset + PAGE, docs.data.total)} of {docs.data.total}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled={offset + PAGE >= docs.data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </button>
          </nav>
        )}
      </section>
    </div>
  );
}
