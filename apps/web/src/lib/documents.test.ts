import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  PARSE_LABELS,
  MATCH_LABELS,
  coverageText,
  formatBytes,
  isActiveDoc,
  matchSummary,
  uploadDocument,
  usageText,
  validateFileLocally,
  warningTitle,
} from "./documents";
import type { DocumentRecord } from "./types";

const MAX = 50 * 1024 * 1024;
const doc = (over: Partial<DocumentRecord> = {}): DocumentRecord => ({
  id: "d1",
  paper_id: null,
  paper_title: null,
  original_filename: "a.pdf",
  size_bytes: 1000,
  sha256: "x",
  parse_status: "parsed",
  error_code: null,
  error_message: null,
  page_count: 10,
  text_version: 1,
  pages_text: 10,
  pages_ocr: 0,
  pages_blank: 0,
  pages_unreadable: 0,
  text_coverage: 1,
  pdf_metadata: {},
  warnings: [],
  scan_status: "not_scanned",
  scan_detail: null,
  match_status: "unmatched",
  match_method: null,
  match_score: null,
  candidates: [],
  needs_confirmation: false,
  matched_at: null,
  can_retry: false,
  can_download: true,
  created_at: "2026-10-24T10:00:00Z",
  processed_at: null,
  row_version: 1,
  ...over,
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [1023, "1023 B"],
    [1024, "1.0 KB"],
    [1536, "1.5 KB"],
    [5 * 1024 * 1024, "5.0 MB"],
    [150 * 1024 * 1024, "150 MB"],
    [2 * 1024 ** 3, "2.0 GB"],
  ])("%d -> %s", (n, s) => expect(formatBytes(n)).toBe(s));
});

describe("validateFileLocally", () => {
  const ok = { name: "paper.pdf", size: 1000, type: "application/pdf" };
  it("accepts a plausible PDF, including generic or empty types", () => {
    expect(validateFileLocally(ok, MAX)).toBeNull();
    expect(validateFileLocally({ ...ok, type: "" }, MAX)).toBeNull();
    expect(validateFileLocally({ ...ok, type: "application/octet-stream" }, MAX)).toBeNull();
    expect(validateFileLocally({ ...ok, name: "UPPER.PDF" }, MAX)).toBeNull();
  });
  it("rejects with a specific message", () => {
    expect(validateFileLocally({ ...ok, name: "paper.docx" }, MAX)).toMatch(/\.pdf/);
    expect(validateFileLocally({ ...ok, name: "paper.pdf.exe" }, MAX)).toMatch(/\.pdf/);
    expect(validateFileLocally({ ...ok, size: 0 }, MAX)).toMatch(/empty/);
    expect(validateFileLocally({ ...ok, size: MAX + 1 }, MAX)).toMatch(/limit/);
    expect(validateFileLocally({ ...ok, type: "image/png" }, MAX)).toMatch(/not a PDF/);
  });
  it("accepts a file exactly at the limit", () => {
    expect(validateFileLocally({ ...ok, size: MAX }, MAX)).toBeNull();
  });
});

describe("status helpers", () => {
  it("knows which documents are still being read", () => {
    expect(isActiveDoc({ parse_status: "queued" })).toBe(true);
    expect(isActiveDoc({ parse_status: "processing" })).toBe(true);
    expect(isActiveDoc({ parse_status: "parsed" })).toBe(false);
    expect(isActiveDoc({ parse_status: "failed" })).toBe(false);
  });
  it("has a label for every status the backend can send", () => {
    expect(Object.keys(PARSE_LABELS).sort()).toEqual(["failed", "parsed", "processing", "queued"]);
    expect(Object.keys(MATCH_LABELS)).toHaveLength(5);
  });
  it("never implies full coverage when pages are unreadable", () => {
    expect(coverageText(doc())).toBe("10 of 10 pages have text");
    const partial = doc({ pages_text: 6, pages_ocr: 2, pages_blank: 1, pages_unreadable: 1 });
    expect(coverageText(partial)).toBe("8 of 10 pages have text, 2 read by OCR, 1 blank, 1 unreadable");
    expect(coverageText(doc({ parse_status: "failed" }))).toBeNull();
    expect(coverageText(doc({ page_count: null }))).toBeNull();
  });
  it("summarises matches and usage", () => {
    expect(matchSummary(doc())).toBe("Not linked to a paper");
    expect(matchSummary(doc({ match_status: "confirmed", paper_title: "A study" }))).toBe(
      "Confirmed by you: A study",
    );
    expect(usageText(5 * 1024 * 1024, 2 * 1024 ** 3, 3, 1000)).toBe(
      "5.0 MB of 2.0 GB used · 3 of 1000 files",
    );
  });
  it("gives unknown warning codes a neutral title", () => {
    expect(warningTitle({ code: "file_repaired", message: "m" })).toBe("Damaged file");
    expect(warningTitle({ code: "something_new", message: "m" })).toBe("Note");
  });
});

/* ---- uploadDocument against a fake XMLHttpRequest ---- */

class FakeXhr {
  static last: FakeXhr;
  upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 0;
  responseText = "";
  responseType = "";
  method = "";
  url = "";
  headers: Record<string, string> = {};
  sent: unknown = null;
  constructor() {
    FakeXhr.last = this;
  }
  open(m: string, u: string) {
    this.method = m;
    this.url = u;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  getResponseHeader() {
    return "corr-1";
  }
  send(b: unknown) {
    this.sent = b;
  }
  abort() {
    this.onabort?.();
  }
}

describe("uploadDocument", () => {
  afterEach(() => vi.unstubAllGlobals());
  const file = { name: "Résumé (1).pdf", size: 10, type: "application/pdf" } as unknown as File;

  it("sends the raw file with the original name percent-encoded, and reports progress", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const seen: number[] = [];
    const h = uploadDocument("p1", file, "paper-9", (f) => seen.push(f));
    const x = FakeXhr.last;
    expect(x.method).toBe("POST");
    expect(x.url).toBe("/api/v1/projects/p1/documents?paper_id=paper-9");
    expect(x.headers["X-SRS-CSRF"]).toBe("1");
    expect(x.headers["Content-Type"]).toBe("application/pdf");
    expect(x.headers["X-Filename"]).toBe(encodeURIComponent("Résumé (1).pdf"));
    expect(x.sent).toBe(file);
    x.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
    x.upload.onprogress?.({ lengthComputable: false, loaded: 1, total: 0 });
    expect(seen).toEqual([0.5]);
    x.status = 202;
    x.responseText = JSON.stringify({ id: "d1" });
    x.onload?.();
    await expect(h.promise).resolves.toEqual({ id: "d1" });
  });

  it("uses no paper query when the file is not for a specific paper", () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    uploadDocument("p1", file, null, () => {});
    expect(FakeXhr.last.url).toBe("/api/v1/projects/p1/documents");
  });

  it("turns the server's error envelope into the same ApiError as every other call", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const h = uploadDocument("p1", file, null, () => {});
    const x = FakeXhr.last;
    x.status = 409;
    x.responseText = JSON.stringify({
      error: { code: "duplicate_document", message: "Already there.", details: null },
    });
    x.onload?.();
    await expect(h.promise).rejects.toMatchObject({
      status: 409,
      code: "duplicate_document",
      message: "Already there.",
    });
  });

  it("explains a proxy 413 page that is not our JSON", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const h = uploadDocument("p1", file, null, () => {});
    FakeXhr.last.status = 413;
    FakeXhr.last.responseText = "<html>413 Request Entity Too Large</html>";
    FakeXhr.last.onload?.();
    await expect(h.promise).rejects.toMatchObject({ status: 413, code: "file_too_large" });
  });

  it("reports network failures and cancellation distinctly", async () => {
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const a = uploadDocument("p1", file, null, () => {});
    FakeXhr.last.onerror?.();
    await expect(a.promise).rejects.toMatchObject({ code: "network_error" });
    const b = uploadDocument("p1", file, null, () => {});
    b.abort();
    const err = await b.promise.catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("aborted");
  });
});
