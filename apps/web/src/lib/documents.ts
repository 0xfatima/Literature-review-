/** Pure helpers for the Files (PDF upload) UI, plus the XHR upload. The server validates every upload again; the
 *  checks here only give faster feedback. Limits come from the API (GET .../documents/summary), never from constants. */
import { ApiError, toApiError } from "./api";
import type { DocumentRecord, DocumentWarning, MatchStatus, PageExtraction, ParseStatus } from "./types";

export const PARSE_STATUSES: ParseStatus[] = ["queued", "processing", "parsed", "failed"];
export const MATCH_STATUSES: MatchStatus[] = [
  "unmatched",
  "needs_confirmation",
  "auto_matched",
  "user_linked",
  "confirmed",
];

export const PARSE_LABELS: Record<ParseStatus, string> = {
  queued: "Waiting to be read",
  processing: "Reading…",
  parsed: "Text extracted",
  failed: "Could not be read",
};

export const MATCH_LABELS: Record<MatchStatus, string> = {
  unmatched: "Not linked to a paper",
  needs_confirmation: "Needs your confirmation",
  auto_matched: "Matched automatically",
  user_linked: "Linked by you",
  confirmed: "Confirmed by you",
};

export const EXTRACTION_LABELS: Record<PageExtraction, string> = {
  text: "Text layer",
  ocr: "Read by OCR",
  blank: "Blank page",
  unreadable: "Could not be read",
};

export const WARNING_TITLES: Record<string, string> = {
  image_only_pages: "Pages without text",
  ocr_used: "Read by OCR",
  ocr_low_confidence: "Low OCR confidence",
  ocr_page_limit: "OCR page limit reached",
  ocr_budget_exhausted: "OCR ran out of time",
  ocr_page_failed: "OCR failed on some pages",
  garbled_text_layer: "Corrupted text layer",
  page_failed: "Pages that could not be read",
  file_repaired: "Damaged file",
  active_content: "Scripts or attachments inside the PDF",
  content_may_not_match: "May not be this paper",
  scan_failed: "Not scanned for malware",
  reprocess_failed: "Re-reading failed",
};

export function warningTitle(w: DocumentWarning): string {
  return WARNING_TITLES[w.code] ?? "Note";
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export const isActiveDoc = (d: Pick<DocumentRecord, "parse_status">): boolean =>
  d.parse_status === "queued" || d.parse_status === "processing";

/** Fast, local feedback. Returns a message, or null when the file looks acceptable (the server decides). */
export function validateFileLocally(
  file: { name: string; size: number; type: string },
  maxBytes: number,
): string | null {
  if (!/\.pdf$/i.test(file.name)) return "Only PDF files can be uploaded. The file name must end in .pdf.";
  if (file.size === 0) return "The file is empty (0 bytes).";
  if (file.size > maxBytes)
    return `The file is ${formatBytes(file.size)}; the limit is ${formatBytes(maxBytes)}.`;
  const t = (file.type.split(";")[0] ?? "").trim().toLowerCase();
  if (t && !["application/pdf", "application/x-pdf", "application/octet-stream"].includes(t))
    return `The file is a ${t}, not a PDF.`;
  return null;
}

/** "5 of 12 pages read by OCR, 1 unreadable" style summary; never implies full coverage when it is not. */
export function coverageText(d: DocumentRecord): string | null {
  if (d.parse_status !== "parsed" || d.page_count === null) return null;
  const parts = [`${d.pages_text + d.pages_ocr} of ${d.page_count} pages have text`];
  if (d.pages_ocr) parts.push(`${d.pages_ocr} read by OCR`);
  if (d.pages_blank) parts.push(`${d.pages_blank} blank`);
  if (d.pages_unreadable) parts.push(`${d.pages_unreadable} unreadable`);
  return parts.join(", ");
}

export function matchSummary(d: DocumentRecord): string {
  if (d.paper_title) return `${MATCH_LABELS[d.match_status]}: ${d.paper_title}`;
  return MATCH_LABELS[d.match_status];
}

export function usageText(usedBytes: number, quotaBytes: number, files: number, quotaFiles: number): string {
  return `${formatBytes(usedBytes)} of ${formatBytes(quotaBytes)} used · ${files} of ${quotaFiles} files`;
}

export interface UploadHandle {
  promise: Promise<DocumentRecord>;
  abort: () => void;
}

/** Upload one file as the raw request body (see the API's upload contract) and report real byte progress. */
export function uploadDocument(
  projectId: string,
  file: File,
  paperId: string | null,
  onProgress: (fraction: number) => void,
): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<DocumentRecord>((resolve, reject) => {
    const q = paperId ? `?paper_id=${encodeURIComponent(paperId)}` : "";
    xhr.open("POST", `/api/v1/projects/${projectId}/documents${q}`);
    xhr.responseType = "text";
    xhr.setRequestHeader("X-SRS-CSRF", "1");
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* a proxy error page, not our JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as DocumentRecord);
      if (xhr.status === 413 && body === null)
        return reject(
          new ApiError(413, "file_too_large", "The file is larger than the server accepts.", null, null),
        );
      reject(toApiError(xhr.status, body, xhr.getResponseHeader("x-correlation-id")));
    };
    xhr.onerror = () =>
      reject(
        new ApiError(
          0,
          "network_error",
          "The upload could not be completed. Check your connection.",
          null,
          null,
        ),
      );
    xhr.onabort = () => reject(new ApiError(0, "aborted", "Upload cancelled.", null, null));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}
