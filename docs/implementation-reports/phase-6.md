# Phase 6 report: PDF upload and full-text processing (2026-10-24)

## Definition of done and status

> A user can upload a PDF, see its processing status, inspect extracted text with page references, and associate
> it with the correct paper. Failed files produce understandable errors rather than silently disappearing.

| Criterion | Implementation | Verified |
|---|---|---|
| Drag-and-drop and file-picker upload with progress | XHR raw-body upload with `onprogress`; dropzone in the Files tab; cancel at any point | Integration + Playwright spec |
| Extension / MIME / magic bytes / size / empty file validation | `app/documents/validation.py`; server-side check; client pre-check for fast feedback | 90 unit tests |
| Safe storage key, never the user filename | Key `projects/{uuid}/documents/{uuid}.pdf`; symlink-escape guard; 0600 files, 0700 dirs; atomic write | Integration + unit tests |
| SHA-256 hash; duplicate rejection | Hash streamed during intake; unique partial index `(project_id, sha256) WHERE removed_at IS NULL` | Integration tests |
| Match PDF to paper records | DOI, arXiv id, title/author. Uncertain → `needs_confirmation`, never auto-linked | Integration + unit tests |
| Page-aware text extraction | PyMuPDF in a sandboxed child process; page text committed to `document_pages` | Integration + pipeline tests |
| OCR only for image-only pages | Pages with images and <25 text chars → `needs_ocr`; Tesseract via `OcrEngine`; confidence per page | Integration + pipeline + real Tesseract |
| Extraction warnings, metadata, page counts | `warnings` JSONB on `paper_documents`; `extraction` in {text, ocr, blank, unreadable} | All suites |
| Encrypted / corrupt / timeout failures | `password_protected` (not retryable), `parser_crashed`, `parse_timeout` (retryable), etc. | Integration + pipeline tests |
| Processing status visible; retry works | `parse_status` in {queued, processing, parsed, failed}; retry increments `text_version`; old pages kept | Integration tests |
| View and download the original file | Page-by-page text viewer; attachment-only download with `Content-Security-Policy: sandbox` | Integration + Playwright spec |
| Quotas, scan, soft removal, orphan sweep | Per-project/user byte and file quotas; ClamAV adapter; 30-day retention; scheduled purge task | Integration tests |

## What was built

**Storage adapter** (`app/providers/storage/`). `ObjectStore` protocol with a local-FS adapter (atomic writes,
0600/0700, traversal guard) and an in-memory adapter for tests. `build_store(settings)` selects the adapter.

**Malware scanner adapter** (`app/providers/malware/`). `MalwareScanner` protocol. `ClamdScanner` speaks the
`zINSTREAM` protocol over TCP; `NullScanner` records `not_scanned` without blocking. `MALWARE_SCAN_REQUIRED=true`
fails a file that cannot be scanned; false (the default) proceeds and warns.

**Document package** (`app/documents/`).
- `validation.py`: extension, declared MIME, `%PDF-` magic bytes, empty file, size cap, sanitised display filename.
- `intake.py`: streams the request body to a private temp file while computing the SHA-256 and checking the magic bytes early; enforces the size cap while streaming, so no oversize file is ever fully buffered.
- `pdf_child.py`: a standalone script started with `python -I` (no user site, no `PYTHON*` environment variables, minimal `PATH`); applies POSIX resource limits; emits JSON lines on stdout. Runs `extract` (page text) or `render` (page PNG for OCR).
- `runner.py`: supervises children with a wall-clock timeout, output-size cap, periodic heartbeat tick, and process-group kill.
- `ocr.py`: `OcrEngine` protocol. `TesseractOcr` renders pages to greyscale PNGs (in the child) then calls `tesseract … tsv`; the TSV is parsed back into text with a mean confidence score.
- `pipeline.py`: orchestrates the child, classifies each page (text / blank / needs\_ocr / garbled), runs OCR where needed, builds warnings, and raises `ProcessingFailure` when the document has no usable text.
- `matching.py`: pure functions. Extracts DOIs (trailing-bracket-aware) and arXiv ids, computes title similarity over a sliding window, checks author surnames. A DOI that appears only as a citation is not a reliable signal without additional corroboration.
- `text.py`: strips NUL and control characters for PostgreSQL; detects garbled text layers by the share of replacement characters and `(cid:N)` placeholders.

**Service layer**: `document_service.py` (API side: register, quotas, list, detail, pages, retry, link, remove, download, purge, orphan sweep, `full_text_status` derivation) and `document_executor.py` (worker side: fetch → checksum → scan → parse → match → commit, all in one transaction).

**Jobs**: `document.process` (max 2 attempts, not cancellable: result is atomic); `system.purge_documents` (beat-scheduled). The sweeper reconciles a document stuck in `processing` after worker loss.

**API** (all behind `get_owned_project`; prefix `/api/v1/projects/{id}`):

| Route | Purpose |
|---|---|
| `GET /documents/summary` | Limits, quotas, per-status counts |
| `POST /documents[?paper_id=]` | Upload one PDF (raw body, `X-Filename` header) |
| `GET /documents` | List with filters (status, match, filename, pagination) |
| `GET /documents/{id}` | Detail |
| `GET /documents/{id}/pages` | Extracted text, page by page |
| `GET /documents/{id}/download` | Original file as an attachment (streamed) |
| `POST /documents/{id}/retry` | Re-queue failed or OCR-upgradeable documents |
| `POST /documents/{id}/link` | Link to a paper, confirm a suggestion, or unlink |
| `DELETE /documents/{id}` | Soft remove |

**Frontend** (`apps/web/src/`):
- `lib/documents.ts`: pure helpers (format, validate, XHR upload with progress and abort).
- `components/documents/Uploader.tsx`: sequential upload queue hook (`useUploader`) and list component (`UploadQueue`).
- `components/documents/DocumentsPanel.tsx`: Files tab (dropzone, usage, filters, document cards with status/warnings/match/retry/remove/download, page-by-page text viewer).
- Modified: sidebar (live file list), workspace (Files tab), papers panel (per-paper upload section).

**Infrastructure**: nginx 55 MB upload limit on the upload route only; shared `storage` named volume in compose; Tesseract in the worker Dockerfile; Tesseract install and `REQUIRE_OCR_TESTS=1` in CI.

## Design decisions (see ADR amendment A13 for full rationale)

| Decision | Alternative rejected |
|---|---|
| Raw HTTP body upload | multipart (spools to disk before size check) |
| Child-process sandboxing for parsing | In-worker (untrusted PDF can OOM or hang the worker) |
| OCR only for image-only pages | Always-on OCR (expensive, less accurate than a real text layer) |
| Cited-DOI is not a reliable match signal without corroboration | Auto-matching on any DOI found anywhere in the text |
| Generated storage key; filename is display text only | User filename as key (path traversal, collisions, information leakage) |
| Soft removal with retention | Hard delete (irreversible; loses citation evidence) |
| `full_text_status` derived from documents | Set from provider access claims |

## Tests run, results and coverage

| Suite | Result |
|---|---|
| Backend unit + integration (real PostgreSQL 16 + Redis 7) | **519 passed, 0 skipped, 0 failed** (was 327; +192) |
| Coverage (`app/`) | **87%** |
| `app/documents/intake.py`, `text.py`, `validation.py`, `matching.py` | 99–100% |
| `app/providers/storage/base.py`, `memory.py` | 100% |
| `app/providers/storage/local.py` | 96% |
| `app/providers/malware/` | 93–100% |
| `app/documents/runner.py` | 99% |
| `app/documents/pipeline.py`, `ocr.py` | 87% |
| `app/services/document_executor.py` | 90% |
| `app/services/document_service.py` | 81% |
| `app/documents/pdf_child.py` | 0% by design (runs as subprocess; covered end-to-end) |
| Vitest (`src/lib`) | **55 passed** (was 35; +20 including XHR upload tests with a fake `XMLHttpRequest`) |
| Ruff, ruff format, mypy --strict (137 files) | Clean |
| validate_static.py, check_erd.py | All passed |
| Migration round-trip | 0 diffs vs ORM; reversible |
| next build | Clean |
| Mutation check (2 rules removed) | Caught by exactly the intended tests |
| nginx live test (real nginx 1.24 + uvicorn): 40 MB → 202, 52 MB → API 413, 60 MB → nginx 413 | Confirmed |

**CI floor raised from ≥320 to ≥510.**

**Not run in this sandbox:** Playwright (Chromium not downloadable; spec written and type-checked); `docker compose up` (no Docker daemon; changes code-reviewed only); live ClamAV (clamd adapter tested against a fake TCP server); CI Tesseract (tested locally).

## Known bugs, gaps, security notes, limitations

- **PyMuPDF AGPL-3.0 or Artifex commercial licence** (see ADR A13.8). Confirm compatibility before deploying.
- **S3 storage not implemented.** `STORAGE_BACKEND=s3` is refused at startup.
- **Reading order not guaranteed** for multi-column PDFs (depends on the PDF's character stream order).
- **ClamAV `StreamMaxLength`** must be ≥ `UPLOAD_MAX_BYTES`; not enforced at startup.
- **No conditional-GET (`ETag`/`Last-Modified`)** on downloads.
- **`scan_status` is not refreshed** when ClamAV signature databases update; older files are not re-scanned.
- **`documents_for_paper_id` is called on every paper detail request** (one extra query per paper opened in the Papers tab). Fine at this scale; cache or a join if the Papers tab becomes slow.
- **No dual-reviewer support** for document confirmation.

## Migrations, configuration, deployment

- `alembic upgrade head` applies `0006_documents` (reversible to `0005_relevance_screening`).
- 30 new settings; see `.env.example` and `docs/architecture/environment-configuration.md`.
- **Shared storage volume required.** In compose the `storage` named volume is mounted at `/srv/storage` on both `api` and `worker`; `STORAGE_LOCAL_ROOT=/srv/storage` is set in the `dockerenv` block. Outside compose, mount or symlink the same directory on both processes.
- Worker image installs `tesseract-ocr` and `tesseract-ocr-eng`. Additional language packs: add to the Dockerfile and list them in `OCR_LANGUAGES`.

## Recommended next phase

**Phase 7: full-text screening.** Second-stage screening using the uploaded PDFs (`screening_decisions.stage = 'full_text'`), required exclusion reasons at the full-text stage, PRISMA 2020 counts (from `record_status_events`), and the full-text screening queue with its own filters and bulk actions.
