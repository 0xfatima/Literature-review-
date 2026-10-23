# Phases 4-5: tests and fixes (2026-10-23)

Phases 4 and 5 were delivered without tests (testing was deferred on request). This change adds them, fixes the one
existing test that Phase 4 broke, and fixes the defects the new tests found.

## Results (actually run)

| Suite | Result |
|---|---|
| Backend pytest, unit + integration, real PostgreSQL 16 + Redis 7, `REQUIRE_INTEGRATION=1` | **327 passed, 0 skipped, 0 failed** (before: 189) |
| Line + branch coverage (`app/`) | **87%** |
| LangGraph gate (real Celery worker subprocesses), included above | 6/6 |
| Stdlib-only runner | 71 passed |
| Vitest (`src/lib`) | 29 passed |
| ruff, ruff format, mypy --strict (117 files) | clean |

Coverage of the new modules:

| Module | Coverage |
|---|---|
| `search/dedup` | 100% |
| `search/query` | 97% |
| `search/records` | 94% |
| `search/derive` | 90% |
| `providers/scholarly/http` | 98% |
| Scholarly adapters | 85-94% |
| `search_service` | 86% |
| `search_executor` | 77% |
| `screening/decisions` | 100% |
| `screening/relevance` | 97% |
| `screening/ai_assessment` | 96% |
| `screening_service` | 96% |
| `relevance_service` | 87% |
| `providers/llm/gemini` | 86% |
| `api/v1/search`, `api/v1/screening` | 100% |

CI floor raised: backend `tests >= 320`.

**Not run:**
- live provider or Gemini calls (providers are mocked at the HTTP layer with responses written by hand in each
  provider's documented format, in `tests/fixtures/scholarly.py`);
- Playwright;
- `middleware.test.ts` (needs Next.js; unchanged).

## The failing test on a clean checkout

`test_authorization_matrix.py::test_owner_can_read_every_object_route_used_above` failed. Phase 4 had given the
fixture made-up UUIDs for `search_run_id` / `paper_id`, and this test (correctly) refuses ids that do not exist.

The fixture now builds real objects: an approved protocol (driven through the real workflow), a real search run
(provider HTTP mocked) and real papers. A new test also checks that search runs and papers are unreachable through
another project of the same owner.

## Defects found by the new tests (all fixed)

1. **Searching during a protocol revision** was refused with "not reached the search stage". It now reports
   `workflow_busy`.
2. **The screening queue was unreadable during a revision.** The run is back at PROTOCOL_REVIEW, so reads failed.
   Reads now judge `stage_before_revision`; writes are still refused with `workflow_busy`.
3. **Empty filter values** (`?decision=`, `?band=`, `?access=`) returned 422. They now mean "no filter".
4. **Wrong status for another project's ids.** A run or paper id from another project of the same owner could get 409
   instead of 404 (search retry, screening history and decision). The object lookup now comes first.
5. **Misleading parser message.** `a OR NOT b` was rejected with "only excludes terms"; it now gets its own message.
6. **Migration round-trip test side effect.** The test drops `lg_checkpoints` while the test process has cached
   LangGraph's checkpointer setup, so later workflow tests failed with `UndefinedTable`. This was hidden until a
   workflow ran earlier in the same session. The test now clears the cache after re-upgrading, and also covers the
   Phase 4/5 tables and the `screening_decision_guard` trigger function.

## New test files

| File | Covers |
|---|---|
| `tests/fixtures/scholarly.py` | Provider responses in documented formats; a routing mock transport with failure injection |
| `tests/fakes.py` | A no-wait HTTP fetcher; a fake LLM provider |
| `unit/test_search_query.py`, `test_search_derive.py`, `test_search_records_dedup.py` | Query language, derivation, normalisation, deduplication |
| `unit/test_scholarly_adapters.py`, `test_throttle.py` | All four adapters, paging, malformed bodies, retries and their limits, timeouts, size cap, throttles |
| `unit/test_relevance_lexical.py`, `test_ai_assessment.py`, `test_screening_rules.py`, `test_llm_provider.py` | Phase 5 rules, AI-answer validation, Gemini adapter, gateway |
| `integration/test_search.py` (15 tests) | Plan, provenance and deduplication, redelivery, partial failure and retry, cursor resume, cap, skipped records, broaden gate, one active search, cancellation (queued and running), superseded protocol, sweep after worker loss, rate limit, cascade |
| `integration/test_screening.py` (15 tests) | Preconditions; decide, revise and recover; invalid decisions; required reason; version conflict; append-only trigger; bulk with safe undo; single undo; revision lock; word overlap; fake-LLM suggestions are never decisions; quota stop and resume; partial answers; superseded protocol |
| `integration/support.py` | Runs `search.run` / `relevance.assess` in-process (real task logic; only the broker hop skipped) |

## How to run

```
docker compose up -d postgres redis   # or any PostgreSQL 16 + Redis
export TEST_DATABASE_URL=postgresql+psycopg://srs:<pw>@localhost:5432/srs_test   # name must contain "test"
export REDIS_URL=redis://localhost:6379/15 REQUIRE_INTEGRATION=1
pytest services/api/tests -q
```
