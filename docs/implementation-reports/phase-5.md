# Phase 5 report: relevance ranking and human screening (2026-10-23)

## Definition of done and status

> The user can screen real records, revise decisions, filter the results, and recover all previous decisions after
> reopening the project. AI suggestions and human decisions remain distinguishable.

| Criterion | How it is implemented | Verified here |
|---|---|---|
| Screen real records | The Screening tab lists the project's discovered papers (Phase 4) with title, authors, year, venue, abstract, source and relevance. Include, Exclude (with reason) and Uncertain buttons, plus I/E/U keys. | Code path only |
| Revise decisions | A new decision appends a row that supersedes the previous one. History is per record. Undo is available for a single decision or a bulk batch. | Code path only |
| Filter results | Filters for decision state, relevance band, AI suggestion and text; five sort orders; pagination. | Code path only |
| Recover decisions after reopening | Every decision commits immediately. The default view, "Still to screen", resumes where you stopped. | Code path only |
| AI and human distinguishable | AI output lives only in `relevance_assessments`, shown as "AI suggests: …" with "a suggestion only". Decisions live only in `screening_decisions`, always with a human reviewer. Each decision records which assessments were on screen. | Code path and validation rules (offline sanity) |

**Update:** tests were added afterwards; see `phase-4-5-tests.md` (327 backend tests passing, 87% coverage). Original note: **Testing was deferred at your request.** No pytest, Vitest or Playwright suite was run, and no new tests were
written. Only static checks and an offline sanity script were run (below). **The Gemini adapter was not called
against Google's API:** this sandbox cannot reach it, and your key was deliberately not used anywhere. It was written
against the documented `generateContent` API and exercised with mocked responses.

## Plan (as executed)

- **Modules:**
  - `app/providers/llm/` (gateway and Gemini adapter);
  - `app/screening/` (lexical scorer, AI prompt, schema and validator, decision rules);
  - `relevance_service`, `screening_service` and `repositories/screening`;
  - schemas and `api/v1/screening.py`;
  - migration `0005`;
  - the `relevance.assess` task;
  - the Screening tab.
- **Dependencies:** none new (httpx is already a runtime dependency since Phase 4).
- **Risks:**
  - free-tier quotas and model-name churn: the model is a setting, requests are batched and throttled, and quota
    errors stop cleanly and can be resumed;
  - AI over-reach: suggestion only, deterministic validation, never a decision;
  - concurrent edits: optimistic concurrency and safe undo.

## What was built

**LLM layer (`app/providers/llm/`).**
- `LLMGateway` is the single entry point and enforces data classes: `public_metadata` only; `private_document` is
  refused.
- `GeminiProvider` calls `generateContent` with `responseSchema` JSON output.
  - The key goes in the `x-goog-api-key` header, never in a URL.
  - Retries cover timeouts, 429 and 5xx, honouring Retry-After and Google's RetryInfo `retryDelay`.
  - 400/401/403/404 are reported clearly and not retried.
  - Request spacing is shared across workers (Redis).
- `describe()` tells the UI what is configured and what is sent, without exposing the key.

**Relevance (separate from eligibility).**
- **Lexical, `lexical-v1`:** each framework search concept (synonyms OR-ed) and each approved research question is
  matched against the title, keywords and abstract, using a small suffix-stripping stemmer and field weights.
  - The explanation lists, per concept, the matched words, where they matched, and the missing words.
  - Records with no abstract and a short title get the band `insufficient`.
- **AI, `relevance-v1`:** about 10 records per request with the objective, questions and accepted criteria. The model
  returns a 0-100 relevance score with a reason, per-question relevance, an optional suggestion, and criterion
  judgements with quotes. Deterministic validation then:
  - accepts only records and criteria that were in the request;
  - keeps a quote only if it occurs verbatim in the record;
  - shows "exclude" as "uncertain" unless a verified quote shows a criterion violated;
  - shows "uncertain" when information is insufficient.
- **Bands:**

  | Band | Colour | Marker |
  |---|---|---|
  | High | green | ●●● |
  | Moderate-to-high | lime | ●●○ |
  | Possible | light green | ●○○ |
  | Low | grey | ○○○ |
  | Insufficient metadata | grey | – |
  | Not yet assessed | grey | – |

  Every band is shown with its text label, the score, and the source ("AI" or "word overlap"). Colour is never the
  only signal.

**Screening.**
- Decisions are include / exclude / uncertain.
- **Exclusion reasons** come from the approved protocol ("meets exclusion criterion …", "does not meet inclusion
  criterion …") plus generic reasons (off topic, wrong publication type, duplicate, other). "Other" requires a note.
- At title/abstract stage a reason is recommended; set `SCREENING_TA_REQUIRE_EXCLUSION_REASON=true` to require one.
- **Each decision stores** the reviewer, time, reason code, a snapshot of the reason text, the note, the protocol
  version, and the assessment ids shown.
- **Bulk actions** cover up to 200 records with one batch id.
- **Undo** restores the previous decision for each record, and skips records changed since.
- **Decisions are append-only, enforced by a database trigger.**
- **Optimistic concurrency** on single decisions.
- A decision made under an earlier protocol version is flagged "review it".
- **Stage move:** the first decision moves the workflow from SEARCH to RECORD_SCREENING and the project to
  `screening`.

**Background job `relevance.assess`.**
- Lexical scoring always runs first: deterministic, no network, committed every 200 records.
- The AI part runs only when the user asks for it and has ticked the acknowledgement of what is sent.
  - It works best lexical score first, in batches.
  - Each request is one `model_runs` row, success or failure.
  - A quota or rate limit, a rejected key or model, or repeated failures stop it cleanly; word-overlap scores are
    kept and the user can continue later.
- The job is cancellable between batches and has a time budget.

**API** (all behind `get_owned_project`; prefix `/api/v1/projects/{id}/screening`):

| Route | Purpose |
|---|---|
| `GET /queue` | Queue with filters: decision, band, AI suggestion, text, possible duplicates; sort; paging; progress counts |
| `GET /reasons` | Exclusion reasons from the approved protocol |
| `GET /papers/{paper_id}/history` | A record's decisions and assessments |
| `POST /papers/{paper_id}/decision` | Record a decision (optimistic concurrency) |
| `POST /bulk` | Bulk decision (up to 200 records) |
| `POST /undo` | Undo a batch or a single decision |
| `GET /relevance` | Status: configured model, counts, recent model runs, last result |
| `POST /relevance` | Start an assessment (rate-limited per user) |

**UI.**
- Screening tab sections: progress, relevance controls, filters, bulk bar with undo, and record cards. Each card has
  the band chip, "Why this score?" (per-concept word matches, the AI reason, per-question relevance, the AI suggestion
  with criterion judgements and verified quotes, validation warnings), the abstract, decision buttons with
  `aria-pressed` and `aria-keyshortcuts`, an exclusion form, and history with undo.
- The Search tab gains "Start screening"; the status bar labels relevance jobs.

## Design decisions (full record: ADR-0001 amendment A12)

1. **Gemini free tier, with the model set by configuration.** The example is `gemini-2.5-flash-lite`, the
   highest-volume free model at the time of writing. Rejected: hard-coding a model, since names and quotas change.
2. **Public metadata only, through a gateway.** Rejected: calling the provider directly from services.
3. **Relevance and eligibility are separate tables; the AI never writes decisions.** Rejected: auto-excluding
   low-relevance records.
4. **Lexical first.** Rejected for now: embeddings (another provider and quota, and an opaque score).
5. **Append-only decisions plus a current-state pointer.** Rejected: an `is_current` flag on decision rows, which
   would mean updating an immutable log.
6. **Batching, throttling and resumable stops** to fit free-tier quotas.

## Checks actually run (results)

| Check | Result |
|---|---|
| App import and OpenAPI | All 8 screening routes register; the Celery task imports |
| `mypy --strict` (117 files) | Success (2 issues found and fixed) |
| `ruff check`, `ruff format --check` | Clean |
| `scripts/validate_static.py`, `scripts/check_erd.py` | All passed; 0 hard errors |
| `alembic upgrade 0004:0005 --sql` (offline) | Renders. No double-prefixed constraint names; the trigger is created. |
| TypeScript strict on the new and changed UI files | No errors (a planted error is caught, so the check covers these files) |
| Prettier | Formatted |
| Secret scan of the working tree for the key you posted | Not present anywhere |

The offline sanity script (mocked HTTP) covered:
- **Lexical scoring:** concept extraction; a high score with explanation; no abstract giving `insufficient` with low
  confidence.
- **Decision rules:** reason options exclude rejected criteria; "other" needs a note; a reason is refused on
  include; a rejected criterion is refused as a reason.
- **AI validation:** an exclusion with a verified quote is kept; a fabricated quote is removed and "exclude" becomes
  "uncertain"; an unknown record is ignored.
- **Gemini adapter:** a 429 with RetryInfo is retried and then succeeds; tokens are recorded; the key is only in the
  header; a 401 maps to a clear "rejected the API key".
- **Gateway:** `private_document` is refused.

**Not run:** the migration on real PostgreSQL, any test suite, a live Gemini call, ESLint, the Next.js build, Docker.

## Known bugs, gaps, security notes, limitations

- **Your API key was posted in chat. Rotate it** and put the new one only in your local `.env` as `LLM_API_KEY`.
- **About that key:** it starts with `AQ.`, while Gemini keys from Google AI Studio usually start with `AIza`. If
  Gemini answers "rejected the API key" (401/403), create a key in AI Studio.
- **Free-tier data use:** content may be used by Google to improve its products. Only public metadata and the protocol
  are sent, and the UI says so.
- **Free-tier limits** are per project per day and change over time. With the defaults (10 records per request, 4.5 s
  spacing) about 300 records take roughly 30 requests. A quota stop is resumable.
- **Suggestion quality depends on the model;** validation removes unverifiable quotes and evidence-free exclusions but
  cannot make a weak judgement right. Suggestions are never decisions.
- **Model name:** `gemini-2.5-flash-lite` may be renamed or retired; change `LLM_MODEL`. A 404 says "does not offer
  model …".
- **Not yet built:**
  - dual-reviewer screening and conflict resolution;
  - full-text screening (Phase 7);
  - resolving possible duplicates from the queue (they are labelled only);
  - PRISMA counts (Phase 7).
- **Unchanged decision:** re-choosing the same include/uncertain decision is a no-op, so a changed note alone is not
  saved; re-choose Exclude to change its reason or note.
- **Lexical scorer limits:** the stemmer is deliberately simple and English-centred.

## Migrations, configuration, deployment

- `alembic upgrade head` applies `0005_relevance_screening` (reversible).
- New settings (see `.env.example`):

  | Setting | Purpose |
  |---|---|
  | `LLM_PROVIDER` | `none` (default, no model calls) or `gemini` |
  | `LLM_API_KEY` | Secret; local `.env` or a secret store only |
  | `LLM_MODEL` | Model name; no code default |
  | `LLM_BASE_URL`, `LLM_TIMEOUT_S`, `LLM_MIN_INTERVAL_S`, `LLM_MAX_RETRIES` | Connection, timeout, spacing, retries |
  | `LLM_SEND_PRIVATE_DOCUMENTS` | Must stay `false` |
  | `LLM_BATCH_SIZE`, `LLM_MAX_PAPERS_PER_JOB`, `RELEVANCE_JOB_BUDGET_S` | Batching and per-job limits |
  | `RATE_LIMIT_RELEVANCE_PER_HOUR` | Per-user rate limit for starting assessments |
  | `SCREENING_TA_REQUIRE_EXCLUSION_REASON` | Require an exclusion reason at title/abstract stage |

- The worker needs outbound HTTPS to `generativelanguage.googleapis.com` when the LLM is enabled.

## Manual verification

1. In your local `.env`: `LLM_PROVIDER=gemini`, `LLM_API_KEY=<new key>`, `LLM_MODEL=gemini-2.5-flash-lite`. Then
   `alembic upgrade head` and restart the API and worker.
2. Open a project with search results (Phase 4) and go to **Screening**. The progress line shows N records still to
   screen.
3. Click **Score word overlap**. Bands appear; "Why this score?" lists matched and missing words per concept.
4. Tick the acknowledgement, then click **Run AI assessment**. The status bar shows progress. Afterwards, the cards
   show "AI suggests: …" and the reason, and **Recent AI calls** lists model, tokens and duration.
5. Include one record with the I key and exclude one with a protocol reason. Reload: both decisions persist, and the
   "Still to screen" count has dropped by two.
6. Select five records, choose **Mark selected uncertain**, then **Undo**: they return to their previous state. Open a
   record's **History**: every step is listed.
7. Filter: Relevance = High, AI suggestion = Suggests exclude, then sort by least relevant first.
8. Stop the worker or remove the key and run the AI assessment: the job ends with a clear message, and the
   word-overlap scores remain.

## Tests to add before acceptance

- **Unit tests:** lexical concepts and scoring; bands; AI validation (quote verification, exclusion downgrade,
  unknown ids, insufficient information); decision rules; Gemini adapter (success, 429 with RetryInfo, 5xx, 400, 401,
  404, blocked or cut-off output, non-JSON output).
- **Integration tests (Postgres plus a fake provider):** decide, revise and undo, including the skipped-if-changed
  case; append-only trigger (UPDATE and DELETE rejected; project deletion still cascades); optimistic concurrency;
  bulk; stage advance; relevance job lexical-only and with a fake LLM; quota stop and resume; `model_runs` rows.
- **Playwright** (fake provider): screen, reload, filter, bulk and undo, using only the keyboard.

## Recommended next phase

Phase 6: PDF upload and full-text processing (object-storage adapter, hashing, PyMuPDF text with page numbers, OCR
only when needed, linking uploads to papers). Start it in a new chat to keep token use low.
