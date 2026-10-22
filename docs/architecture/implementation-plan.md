# Implementation plan and dependency order

Provisional mapping of your phases to spec milestones (M0–M8). When you issue Phase 1+ instructions I will follow *your* phase definitions and update this table. Each phase: plan first → build vertical slice with tests → run tests → report (rule 15) → stop for your go-ahead.

> **Numbering note (2026-10-23).** The delivery roadmap supplied with the requirements is authoritative for phase
> numbers. Its *Phase 2* is "accounts, projects and persistent chat" (done, see `docs/implementation-reports/phase-2.md`).
> The LangGraph gate, LLM gateway and protocol work listed under "Phase 2" in the table below move to the roadmap's
> next phases; their content and exit criteria are unchanged.

**Dependency chain:** auth/projects → chat+protocol (+LangGraph spike) → search adapters → paper UI/relevance → PDFs → screening → PRISMA → extraction → synthesis/writing → audit/hardening. Nothing later can be correct without the audit/jobs/provenance plumbing from the first phases, so that plumbing is built early, not at the end (rule 8).

## First working slice (Phase 1 target)
**Register → sign in → create project → list/reopen it → sign out, with ownership enforced and every action recorded in `audit_events`, through UI → API → DB.** Small, fully testable, and it fixes the cross-cutting foundations (config, migrations, errors, logging, auth, authorization dependency, job table shell, test harness) that everything else reuses.

## Phases

| Phase | Spec | Slice delivered | Key tasks | Acceptance / exit gate |
|---|---|---|---|---|
| **0** | — | Audit + architecture | ADR, audit, ERD, env plan, skeleton, plan | **Done; ADR Accepted (A1–A8)** |
| **1** | M0 | Auth + projects + foundations (**written; see iteration-001 for what was executed**) | Settings w/ fail-fast; SQLAlchemy base + Alembic baseline (users, auth_sessions, password_reset_tokens, projects, audit_events, jobs); Argon2id; session cookie; CSRF/Origin check; rate limit on login; `get_owned_project`; error envelope + correlation ID; JSON logging w/ redaction; `/healthz` `/readyz`; Dockerfiles + compose; Next shell (login/register/projects/IDE layout skeleton); OpenAPI→TS client; **deletion/retention decided (ERD notes)**; password recovery explicitly deferred (A8) | Fresh-clone setup works; unit tests (hashing, session, redaction, settings); integration tests on real Postgres: ownership (user B cannot read/patch/delete user A's project, by every route), audit written; Playwright: register/login/create/reopen/logout/protected route |
| **2** | M1 | **LangGraph gate first (ADR A7: 6 tests on real Postgres + real worker process)**, then chat + protocol | `LLMGateway` with consent gates (A4, `llm_consents`); `FakeLLM` + Gemini adapter skeleton; prompt files + versioning; conversations/messages; protocol_versions/questions/criteria; Protocol Planner node; approval interrupt; resume API; workflow_runs/model_runs; **crash/resume test before any other node** (R5); criteria edit ⇒ new version | Versioning, immutability-after-approval, resume-after-restart, bounded-retry tests pass; approval recorded with approver/time/version; search endpoint refuses without approved protocol (test) |
| **3** | M2 | Search | `ScholarlySource` interface; OpenAlex + Europe PMC adapters (A2; discipline-agnostic, more adapters pluggable); query builders; search_runs log; pagination/cursor; backoff on 429/5xx; normalisation; DOI/title dedupe + `dedupe_candidates`; idempotent re-run; partial-failure status | Mocked-provider tests: success, empty, malformed, pagination, timeout, 429, 5xx; reproducibility test (same protocol ⇒ same logged queries); dedupe false-merge tests; partial failure never "complete" |
| **4** | M3 | Discovery UI + relevance | Paper list/cards; access-status display; relevance labels w/ text+icon (a11y); rationale; filters; decisions API (supersession); supplementary-discovery opt-in + disclaimer; `FakeLLM`-tested relevance node | Decisions persist across refresh (E2E); missing-abstract ⇒ "not assessed"; no auto-exclude test; axe/keyboard checks on cards |
| **5** | M4 | PDFs + screening | `ObjectStore` local; upload validation; PyMuPDF behind interface; page text; match + confirm flow; OCR flag path; T/A vs full-text stages; FT exclusion reason CHECK | Corrupt/encrypted/empty/oversized/mismatched-MIME tests; page refs correct on fixture PDFs; authorization on documents; loop/backtrack tests |
| **6** | M5 | PRISMA + protocol revision impact | **human verification of `prisma-2020-validation.md` against official sources first**; `record_status_events` + `studies` + pathway/reason codes (A6); invariants V1–V11; derived counts; flow diagram; stale-marking on criteria change; checklist aid (labelled non-certifying) | Count-invariant property tests; impossible totals rejected; diagram blocked on invalid counts |
| **7** | M6 | Extraction | Field chips + default schema + custom fields; schema versions; extractor node; excerpt-exists validator; explicit status enum; editable matrix; per-field rerun; CSV/XLSX export | Provenance tests (quote must occur on cited page); `source_unavailable` vs `not_reported` tests; edit history; small labelled eval set (opt-in live) |
| **8** | M7 | Synthesis + writing | Comparison tables; synthesis versions; draft sections; `claim_evidence_links`; citation verifier; DOCX export | Invalid-citation rejection; DOCX preserves headings/tables/refs; question-coverage indicators never "Answered" from abstracts alone |
| **9** | M8 | Audit + hardening | Audit UI/export; quotas; load/resilience; security review; backup/restore drill; deployment docs; acceptance checklist | Spec §16 checklist fully evidenced by test runs |

## Cross-cutting items scheduled early (not left to the end)
- Phase 1: logging/redaction, error envelope, audit writer, authz dependency, `jobs` table, migrations discipline.
- Phase 2: model_runs recording, prompt versioning, injection-safe prompt templating (data delimiters), idempotency keys.
- Every phase: OpenAPI regeneration check, migration up/down test, report file `docs/implementation-reports/iteration-NNN.md`.

## Standing definition of "phase complete"
Relevant tests *executed* with outcomes recorded (not "app starts"); lint/type checks run; migrations apply on a clean DB; `.env.example` and docs updated; known gaps listed; no mock data without a "Demo data" label.

## Deferred items register
| Item | Deferred to | Reason |
|---|---|---|
| Password-reset email, email verification, MFA | with email infra (post-Phase 1, before any public launch) | A8 |
| OpenAPI → TS client generation + drift check | Phase 2 | needs a runnable API to export the schema |
| Redis rate-limiter test, timing-equivalence measurement | Phase 2 | needs Redis / benchmark environment |
| Account deletion, data export | Phase 9 | not in MVP slice |
| Lockfiles | first networked install | no network when authored |
