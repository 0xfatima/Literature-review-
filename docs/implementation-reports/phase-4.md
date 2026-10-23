# Phase 4 report: real scholarly search engine (2026-10-23)

## Definition of done and status

> A real topic produces real scholarly records with traceable origins, duplicate records are handled correctly, and
> one provider failing does not destroy the entire search session.

| Criterion | How it is implemented | Verified here |
|---|---|---|
| Real records from real sources | OpenAlex, Europe PMC, arXiv and Crossref adapters call the providers' official APIs from the worker. Nothing is generated or sampled. | **Not against live APIs** (this environment has no network route to them, and live calls were avoided on request). Adapters were exercised with mocked HTTP responses in the providers' documented formats. |
| Traceable origins | Every record is stored as returned (`paper_source_records.raw`) with provider, provider record id, rank, run, exact query and retrieval time; papers record which provider supplied each field. | Code path only. |
| Duplicates handled correctly | Identifier match (DOI, PMID, arXiv, OpenAlex), then title + first author + year with no conflicting identifier; same title with any disagreement is flagged, not merged. Unique indexes per project back this up. | Rules exercised on hand-written cases (DOI match, title match, conflicting-author flag). |
| One provider failing does not destroy the session | Each provider has its own query row, status and error. A failure ends only that provider; the run becomes `partial`, never `completed`. "Continue unfinished sources" resumes from the saved cursor. | Retry/backoff path exercised with mocked 429 and 503 responses. |

**Update:** tests were added afterwards; see `phase-4-5-tests.md` (327 backend tests passing, 87% coverage). Original note: **Testing was deferred at your request.** No pytest, Vitest or Playwright suite was run for this phase, and no new
automated tests were written. Only cheap static checks and small offline sanity checks were run (below). Phase 4 must
not be marked accepted until the tests listed under "Tests to add" exist and pass.

## What was built

**Search core (`app/search/`, stdlib only).**
- `query.py`: a provider-neutral Boolean language (AND / OR / NOT in capitals, quoted phrases, parentheses,
  trailing `*`), parsed once. It is rendered per provider, and each rendering lists what it could not express. Field
  tags and pure-exclusion queries are rejected with a clear message.
- `derive.py`: turns the approved protocol into a query. It uses a Boolean search strategy if there is one; otherwise
  the framework's search concepts (synonyms OR-ed, concepts AND-ed; comparator, outcome and design are left for
  screening); otherwise keywords from the objective or topic, flagged for review. It also builds the date and language
  filter plan; study designs are never search filters.
- `records.py`: the common record schema and identifier/title normalisation. It keeps three things apart: the
  open-access status a provider reports, a full-text link it reports, and `full_text_status` (what the application
  holds, always `not_obtained` here).
- `dedup.py`: the conservative matching rules above.

**Providers (`app/providers/scholarly/`).**
- `base.ScholarlySource` interface and a registry; nothing else knows a provider's URL, syntax or response shape.
- `http.HttpFetcher`: timeouts, bounded retries with exponential backoff and jitter, Retry-After, a response size
  cap, and user-safe errors.
- `throttle.RedisThrottle`: cross-worker request spacing, with an in-process fallback.
- Adapters:
  - OpenAlex: cursor paging, `select`, polite pool, optional API key.
  - Europe PMC: cursorMark; filters written in its query syntax; open-access codes OA/F only.
  - arXiv: Atom XML with DTDs refused, 3 s spacing, every record labelled as a preprint.
  - Crossref: keyword-only query (recorded as a limitation); a link counts as open only with a Creative Commons
    licence.
- The contact e-mail and API key are added at request time and are never stored or shown.

**Persistence (migration `0004_scholarly_search`).** `search_runs`, `search_queries`, `paper_records`,
`paper_source_records`, and the `search` kind on `workflow_events`. See `docs/architecture/erd.md` (Phase 4 notes).

**Worker (`search.run` job, `app/services/search_executor.py`).**
- One transaction per page: records, merges and the cursor commit together.
- Source records are unique per (query, provider record id), so a redelivered page adds nothing.
- Per-provider record cap: a query that reports more results is marked `capped`, never `complete`.
- A time budget stops the job cleanly and resumably before the hard task limit.
- Cooperative cancellation between pages.
- The recovery sweep marks a search `failed` if its worker is lost for good; stored pages are kept and the search can
  be continued.

**API** (all behind `get_owned_project`):

| Route | Purpose |
|---|---|
| `GET /projects/{id}/search/providers` | Available sources, tiers and attribution |
| `POST /projects/{id}/search/plan` | Preview of the exact per-provider queries (read-only) |
| `GET /projects/{id}/search/summary` | Identification counts derived from stored records |
| `GET`, `POST /projects/{id}/search-runs` | List runs; start a search (202, background job) |
| `GET /projects/{id}/search-runs/{search_run_id}` | One run with its per-provider queries |
| `POST .../{search_run_id}/retry` | Continue unfinished sources from their cursors |
| `POST .../{search_run_id}/cancel` | Cancel an active search |
| `POST .../{search_run_id}/broaden` | Record yes / no / later for broadening discovery |
| `GET /projects/{id}/papers` | Papers, with filters: text, access, provider, run, possible duplicates, preprints; sort; pagination |
| `GET /projects/{id}/papers/{paper_id}` | One paper with full provenance and the original provider records |

Starting a search is rate-limited per user (`RATE_LIMIT_SEARCH_PER_HOUR`).

**UI.**
- **Search tab:**
  - source choices (sources not in the approved protocol are marked as additions);
  - an editable expression with "reset to the protocol-derived query";
  - a preview of the exact query per source, with filters and limitations;
  - a run button;
  - the broaden card (Yes / No / Later), which unlocks the additional-source plan on Yes;
  - identification counts;
  - search history: per-source exact query, request URL, reported versus retrieved counts, status, errors and last
    retrieval time, with cancel and "continue unfinished sources" buttons and incompleteness warnings.
- **Papers tab:**
  - paper count, filters, sorting and pagination;
  - paper cards with access, preprint, retracted and possible-duplicate labels, the sources a paper was found in, and
    identifier links;
  - an expandable detail view: abstract with its source, the access explanation, and per-source provenance with the
    original record.
  - Metadata-only papers tell the user to obtain a copy they are authorised to use. No upload button is shown, because
    upload has no backend yet.
- **Elsewhere:** the status bar labels `search.run` jobs (cancellable), and Review status offers "Go to search".

## Design decisions (full record: ADR-0001 amendment A11)

1. **Search is a background job, not a LangGraph node.** A node is replayed on resume, which would repeat network
   calls. The per-page commit plus cursor gives equivalent crash safety. The workflow records `search` events at
   stage SEARCH.
2. **The broaden decision is stored on the run, not as a graph interrupt.** The additional tier is refused by the API
   until the answer is "yes".
3. **Tiers.** Open-access-first: OpenAlex, Europe PMC, arXiv. Additional: Crossref. PubMed is reached through Europe
   PMC, not a separate E-utilities adapter.
4. **One query language rendered per provider, with lossy translations recorded.** Rejected: free-text queries per
   provider.
5. **Flag, don't merge, on uncertain duplicates.** Rejected: fuzzy title similarity.
6. **Protocol fidelity.** Each run records the approved protocol version it was built from, which sources were added,
   and which listed sources are unsupported. A run from a superseded version cannot be continued.
7. **One active search per project** (partial unique index), so deduplication merges are serial.

## Checks actually run (results)

| Check | Result |
|---|---|
| App import and OpenAPI route registration | All 10 new routes present |
| `mypy --strict` (106 files) | Success, no issues (33 errors found and fixed during the phase) |
| `ruff check`, `ruff format --check` | Clean |
| `scripts/validate_static.py`, `scripts/check_erd.py` | All passed; 0 hard errors |
| `alembic upgrade/downgrade 0003:0004 --sql` (offline render) | Renders both ways. It caught a double-prefixed constraint name (`ck_workflow_events_ck_workflow_events_kind`) that would have failed on a real database; fixed. |
| TypeScript, strict with `noUncheckedIndexedAccess`, on the new and changed UI files | No errors |
| Prettier on changed web files | Formatted |
| Offline sanity script (mocked HTTP) | Rendering for all 4 providers; parse errors; protocol derivation; DOI/arXiv normalisation; OpenAlex 429 then success; 503 exhausting retries; Europe PMC and arXiv parsing; dedup DOI, title and conflict cases. Found and fixed an arXiv grouping bug. |

**Not run:** the migration against a real PostgreSQL database, any pytest/Vitest/Playwright suite, any call to a live
provider API, Docker Compose, the Next.js build and ESLint.

## Known bugs, gaps, security notes and limitations

- **Unverified against live providers.** Response shapes follow the providers' documentation as known at the time of
  writing. Run the manual verification below before relying on it. OpenAlex's authentication and limits have been
  changing; set `OPENALEX_API_KEY` if your account requires one.
- Possible duplicates are flagged but cannot yet be resolved in the UI (merge or "not a duplicate"). This is planned
  with screening.
- Paper list text search uses `LIKE` (no full-text index); fine for thousands of papers, not for hundreds of
  thousands.
- `raw` records are stored in full in Postgres (OpenAlex records are a few KB each). There is no retention policy yet.
- No supplementary web-search provider (spec 3.3): only official scholarly APIs. The broaden step uses Crossref.
- Provider language metadata is imperfect: a language filter can exclude records without a language.
- The authorization-matrix test uses placeholder UUIDs for `search_run_id` and `paper_id`, because a real run needs
  network access. Ownership is still enforced by the project scope.
- Redis throttle failure degrades to per-process spacing (logged). With several workers and Redis down, the arXiv
  3-second rule could be exceeded.

## Migrations, configuration, deployment

- `alembic upgrade head` applies `0004_scholarly_search` (reversible; the downgrade deletes `search` workflow events).
- `httpx` moved from dev to runtime dependencies (already pinned at 0.28.1 in `requirements.lock.txt`; no lockfile
  change). Rebuild the API and worker images.
- New settings (see `.env.example`): `SEARCH_PROVIDERS_ENABLED`, `SCHOLARLY_CONTACT_EMAIL` (set it), `OPENALEX_API_KEY`,
  `SEARCH_HTTP_TIMEOUT_S`, `SEARCH_MAX_RETRIES`, `SEARCH_BACKOFF_BASE_S`, `SEARCH_MAX_BACKOFF_S`, `SEARCH_PAGE_SIZE`,
  `SEARCH_MAX_RECORDS_PER_PROVIDER` (refused above 10000 outside development), `SEARCH_JOB_BUDGET_S` (keep it below
  `JOB_DEFAULT_TIMEOUT_S`), `RATE_LIMIT_SEARCH_PER_HOUR`.
- The worker needs outbound HTTPS to `api.openalex.org`, `www.ebi.ac.uk`, `export.arxiv.org` and `api.crossref.org`.

## Manual verification

1. `alembic upgrade head`; set `SCHOLARLY_CONTACT_EMAIL`; start the API, worker and web app.
2. Approve a protocol, for example "aerobic exercise versus usual care for depression in adults", with sources
   "OpenAlex, PubMed, Scopus".
3. **Search** tab: OpenAlex and Europe PMC are preselected. Scopus is listed as not searched. The previewed queries
   show the expression in each source's syntax.
4. Lower `SEARCH_MAX_RECORDS_PER_PROVIDER` to 50 for a quick run. Run the search, watch the status bar, and check:
   - each source shows "stopped at record limit";
   - the run shows "Incomplete";
   - records identified equals the sum of retrieved counts, and duplicates removed equals identified minus unique
     papers.
5. **Papers** tab:
   - a paper found in both sources shows two source records with match basis "Matched by DOI";
   - "Show original record" displays the provider JSON;
   - a metadata-only paper asks you to obtain an authorised copy.
6. Failure drill: set an unreachable host for one provider (for example, block `www.ebi.ac.uk` in the worker).
   - The run ends "Incomplete" with that source "Failed" and the other source's records intact.
   - Restore access, click "Continue unfinished sources": no duplicate records appear.
7. Broaden: answer **Later** (the prompt remains), then **Yes**. Run the additional search with Crossref; its
   limitation about Boolean queries is shown.
8. Revise the protocol's scope and approve it. Earlier runs show "built from an earlier protocol version" and cannot
   be continued.

## Tests to add before acceptance

- **Unit tests:** query parser and renderers; derivation; normalisers for each adapter on recorded fixtures (success,
  empty, malformed, pagination end); HTTP retry (timeout, 429 with Retry-After, 5xx, 4xx not retried, oversized
  responses); throttles; dedup (preprint versus published, missing author, short titles).
- **Integration tests (Postgres plus mocked transport):** start, page commit and resume after a simulated crash with
  no duplicates; one provider failing gives `partial`; the cap gives `capped`; cancel; retry; the broaden gate; the
  stale-protocol refusal; the sweep marking searches failed; derived counts.
- **Playwright** (mocked providers): run a search, filter and open papers, and check that choices persist after a
  refresh.

## Recommended next phase

Phase 5: title/abstract screening against the approved criteria, with human decisions as first-class records,
resolution of flagged possible duplicates, and PRISMA identification and screening counts derived from these tables.
