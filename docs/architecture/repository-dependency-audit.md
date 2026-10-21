# Repository and dependency audit (Phase 0)

Audit date: 2026-10-20. Method: shell inspection of the working environment; every statement below was observed, not assumed. Items marked **Unverified** could not be checked offline.

## 1. Repository

| Check | Finding |
|---|---|
| Existing repository | None. `/home/claude` contained only tool caches (`.cache`, `.npm`, `.npm-global`, `.config`, `.local`). No `.git`, no `pyproject.toml`, `package.json` or `docker-compose.yml` anywhere searched (depth 4, excluding system dirs). |
| Git history | None (new repo initialised by this phase on branch `main`). |
| Existing functionality to preserve | None. |
| Conflicting architectural choices | None in code. Conflicts exist only between sources — see §4. |
| Inputs | One file: the requirements `.docx` (61,725 bytes), converted to `docs/requirements/requirements.md` for diffable reference. The original is kept alongside. |

## 2. Toolchain in this sandbox

| Tool | Status | Observed |
|---|---|---|
| Python | ✅ | 3.12.3 (system, PEP 668 "externally managed" — use a venv) |
| pip | ✅ | 24.0 |
| Node / npm / npx | ✅ | 22.22.2 / 10.9.7 |
| git | ✅ | 2.43.0 |
| pandoc | ✅ | 3.1.3 |
| Tesseract OCR | ✅ | 5.3.4 (sandbox only; deployment image must install its own) |
| Docker / docker-compose | ❌ | not installed |
| PostgreSQL (`psql`, server) | ❌ | not installed |
| Redis (server, cli) | ❌ | not installed |
| **Outbound network** | ❌ | `pip install --dry-run fastapi` fails with DNS resolution error; npm registry unreachable. Matches the sandbox config (`network: disabled`). |
| Resources | ⚠️ | 1 CPU, ~3 GB RAM, ~10 GB free disk — adequate for dev/tests of this size; not for load testing (spec §12.4). |

## 3. Dependency status

### 3.1 Python — currently installed (132 packages total; relevant ones)
Only `python-docx 1.2.0` among project-relevant packages. **None** of the following are installed: FastAPI, Uvicorn, SQLAlchemy, Alembic, Pydantic, pydantic-settings, LangGraph (+ postgres checkpointer), Celery, redis-py, psycopg, PyMuPDF, httpx, pytest, argon2-cffi, google-genai/Gemini client.

### 3.2 Planned dependencies (versions **Unverified** — to be pinned from the registry in Phase 1)

| Area | Package(s) | Purpose | Notes |
|---|---|---|---|
| API | fastapi, uvicorn, pydantic v2, pydantic-settings, python-multipart | REST, validation, config, uploads | |
| DB | sqlalchemy 2.x, alembic, psycopg[binary,pool] 3 | ORM/migrations/driver | one driver for app + LangGraph |
| Workflow | langgraph, langgraph-checkpoint-postgres | graph, interrupts, checkpoints | API has changed across releases → pin + spike (R5) |
| Jobs | celery, redis | queue transport | Postgres `jobs` table is truth |
| Auth | argon2-cffi | password hashing | |
| HTTP | httpx, tenacity (or hand-rolled backoff) | provider calls, retries | |
| LLM | Google GenAI SDK (name/version to confirm) | Gemini adapter | behind `LLMClient` |
| PDF | pymupdf | page-aware text | AGPL/commercial licensing — **check licence suitability** before shipping a hosted product |
| OCR (optional) | pytesseract or OCRmyPDF + Tesseract binary | scanned pages | feature-flagged |
| Matching | rapidfuzz | fuzzy title/author match, dedupe | |
| Export | python-docx (present: 1.2.0) or docx-js; openpyxl | DOCX/XLSX/CSV | |
| Quality | ruff, mypy, pytest, pytest-cov, pytest-asyncio (SSE only), respx or httpx MockTransport, freezegun | lint/type/test | |
| Test DB | a real Postgres (see R1) | integration | SQLite is **not** an acceptable substitute (JSONB, partial indexes, checkpointer) |
| Web | next, react, typescript, tailwindcss, @tanstack/react-query, zustand, radix/shadcn, openapi-typescript | UI | |
| Web test | @playwright/test (+ browsers download), vitest + testing-library | E2E/unit | browser binaries need network |

**Licence note (action item):** PyMuPDF is dual-licensed (AGPL / commercial). For a hosted multi-user service this has legal implications; the spec mandates PyMuPDF, so keep it behind a `PdfTextExtractor` interface (cheap) and have the product owner confirm licensing. Flagged, not decided.

## 4. Source-document audit (spec ↔ instructions)

Gaps/ambiguities found in the requirements and how this plan resolves them:

| # | Finding | Resolution |
|---|---|---|
| 1 | Spec data model lacks tables for sessions, reset tokens, jobs, criteria/questions as rows, dedupe candidates, PRISMA status events, decision↔criterion links, page text, evidence revisions, question coverage. | Added (ADR-0001 §4, `erd.md`). |
| 2 | Spec §6 favours async I/O; ADR chooses sync core + workers. | Recorded as deviation D2; confirm (Q3). |
| 3 | Spec §13.1 requires `docs/implementation-reports/iteration-XXX.md`; your rule 15 requires a per-phase report. | Both: file in repo + same content summarised in chat. Phase 0 = `iteration-000.md`. |
| 4 | Spec milestones M0–M8 vs your Phase 0…N. | Provisional mapping in `implementation-plan.md`; I'll follow your phase numbering when you issue it and re-map. |
| 5 | Spec is single-reviewer; systematic-review norms use dual screening. | Documented limitation; `reviewer_id` stored so it can be added. |
| 6 | Spec says "at least two scholarly adapters" but the discipline isn't set. | Provisional OpenAlex + Europe PMC; Q2. |
| 7 | Spec lists no open-access-status source beyond provider fields. Distinguishing OA *metadata* from OA *full text* (spec §3.3) is only as good as provider flags. | Store provider-reported OA fields verbatim + a conservative derived `access_status` (`open_fulltext_link_reported | metadata_only | unknown`); optional Unpaywall-style adapter later. Never infer "open" from free metadata. |
| 8 | Free-tier LLM terms. | R3; flag default off for private documents. |
| 9 | Password reset requires an email path. | `EmailSender` adapter; console in dev. |
| 10 | Deletion/retention not specified beyond "define clearly". | Decide in Phase 1 (see plan). |

## 5. Verdict
Greenfield, no technical debt, no conflicts in code. The principal constraint is **environmental (R1)**, not architectural. Phase 1 should not start until Q1 is answered.
