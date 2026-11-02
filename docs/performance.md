# Performance, capacity and targets (Phase 15)

Everything here was **measured** (2026-10-26) with the harness in `scripts/loadtest/`. Raw output:
`docs/performance/loadtest-8-users.json`. Re-run: `PYTHONPATH=services/api python scripts/loadtest/run.py --users 8`
(needs PostgreSQL + Redis; resets the database named in `LOADTEST_DATABASE_URL`, which must contain "test").

## What the harness exercises (and what it does not)
**Real:** every API route (one client per simulated user, all concurrent), PostgreSQL 16, Redis 7 (rate limits,
provider throttles, the cross-process AI semaphore), the admission pump, the Celery **task functions** run by a pool of
6 worker threads, real HTTP to the providers, PDF parsing in resource-limited child processes, file storage.
**Not real:** the Celery broker hop (an in-process queue; CI's e2e and deploy-verify jobs use the real broker) and the
providers, replaced by `scripts/loadtest/mock_providers.py`, which speaks the documented OpenAlex, Europe PMC and Gemini
formats with injected latency (scholarly ≈50 ms, AI ≈400 ms per call) and a 429 on every 25th request.
**Caveat that matters when reading latencies:** the harness runs all 8 users *and* the 6 job workers in **one Python
process** on one machine, so they compete for one interpreter lock and the same CPUs. The machine's vCPU
count was **not recorded** at measurement time (the sandbox was re-provisioned during the session; afterwards it had
1 vCPU and 4 GB RAM), so absolute times are only comparable within this document. Latencies under load are
pessimistic upper bounds; production runs API and workers in separate containers. Network, TLS and
proxy time are not included (in-process ASGI). Uncontended single-request figures are given alongside.

## Workload (8 users at once, each with their own review)
| Step | Per user | Total |
|---|---|---|
| Scholarly search (OpenAlex 2,000 + Europe PMC 1,500 records, 500 overlapping) | 3,500 records → **3,000 unique papers** | 28,000 records, 149 provider requests |
| Read mix on the 3,000-paper review (7 endpoints × 15 rounds) | 105 requests | 840 |
| Title/abstract screening decisions | 200 | 1,600 |
| PDF upload + parsing (5-page PDFs) | 8 | 64 |
| AI extraction (2 fields) over the 8 parsed PDFs | 1 job | 8 jobs, 68 AI calls |
| Report generation + DOCX export (small accepted review) | 3 + 3 | 24 + 24 |

## Results
### Correctness and isolation under concurrency (definition of done)
- Every project ended with exactly the expected **3,000** unique papers (deduplication across providers and users).
- **0** cross-user leaks: each user listed exactly their own projects; every request for another user's project → 404;
  **0** documents linked across projects; **0** audit events by a non-owner. **0** job errors; all 8 searches, 64 PDF
  jobs and 8 extractions succeeded.
- `test_phase15_concurrency.py` (CI) repeats the isolation check with 6 users × 4 projects in threads and proves that two
  simultaneous edits of one project produce exactly one success and one 409 (no lost update).

### Provider quota protection
| Limit under test | Configured | Observed at the provider (mock) |
|---|---|---|
| AI calls in flight, deployment-wide | 3 (`LLM_GLOBAL_MAX_CONCURRENT_CALLS`) — with 6 workers and a per-process limit of 16 | **peak 3** |
| OpenAlex request spacing (shared across workers) | 0.15 s | peak **6 req/s**, peak 1 concurrent |
| Europe PMC request spacing | 0.20 s | peak **5 req/s** |
| 429 responses | 5 injected | **5 absorbed**: retried after Retry-After, cooldown shared, every search completed |

### Background jobs (from the `jobs` table)
| Job | Count | Queue delay p50 / p95 | Run time p50 / p95 | Notes |
|---|---|---|---|---|
| `search.run` (3,000 papers) | 8 | 0.4 s / 119 s | 117 s / 122 s | p95 delay = 2 users waiting for one of 6 workers. Uncontended single search: **15.8 s** |
| `document.process` (5 pages) | 64 | 16.1 s / 30.1 s | 4.9 s / 5.8 s | fair share: all users finished within 54–58 s of each other |
| `extraction.run` (8 PDFs) | 8 | 0.3 s / 7.6 s | 8.3 s / 9.6 s | bounded by the AI limit of 3, by design |

### Request latency under load (ms; 8 users + 6 workers in one process) vs. uncontended
| Endpoint | p50 | p95 | max | Uncontended (single request) |
|---|---|---|---|---|
| `GET papers` page 1 / last page (3,000 papers) | 136 / 173 | 264 / 371 | 490 | 14 / 21 |
| `GET screening queue` | 331 | 731 | 1,286 | 31 |
| `GET prisma flow` | 554 | 818 | 1,100 | 44 |
| `GET search summary` | 453 | 738 | 917 | 35, 12 SQL statements (before Phase 15: 94, 22 statements)¹ |
| `GET audit events` | 163 | 422 | 536 | 13 |
| `GET jobs` | 110 | 245 | 455 | 10 |
| `POST screening decision` (n = 1,600) | 183 | 245 | 447 | — |
| `POST document upload` (5-page PDF) | 2,799 | 4,015 | — | **31** (the in-process parsing workers dominate the loaded figure) |
| `POST report generate` (small review) | 586 | 685 | 709 | — |
| `POST report export (DOCX)` | 322 | 366 | 446 | — |

¹ Measured after the sandbox was re-provisioned (1 vCPU), so compare the hardware-independent statement counts.

## Optimisations made in Phase 15 (each measured before/after)
| Change | Before | After |
|---|---|---|
| Ingestion: create new papers with their first source counted; one "already stored?" query per page instead of per record | 24,000 SQL statements, 17.7 s for 3,500 records | **17,500 statements (−27 %), 15.8 s (−11 %)**, identical results |
| Search summary: 2 aggregate queries per scope instead of 7 | 43.9 ms, 22 statements | **28.2 ms**, identical output on every project tested |
| Index `jobs(finished_at)` (metrics window), 300k historical jobs | 72 ms (seq scan) | **0.47 ms** |
| Index `model_runs(created_at) INCLUDE (tokens)` (global budget check before every AI call), 200k runs | 46 ms (seq scan) | **12.5 ms** (index-only, last 24 h only) |
| Admission (fair-share) query, 1.2M historical jobs, 403 waiting across 8 users | — | **3.2 ms** per pump; admitted exactly `JOB_MAX_INFLIGHT_PER_USER` (4) per user; uses existing indexes, none added |

## Decisions informed by the measurements
- **Report generation stays synchronous.** p50 0.6 s under load (≤0.7 s p95) and it reads only stored, approved data.
  Re-evaluate if a real review's report exceeds the 2 s target below; the job framework is ready for it.
- **No server-side cache added.** Every hot read is < 100 ms uncontended at 3,000 papers. Rejected: caching PRISMA counts
  or the search summary keyed by a project "generation" (would show stale counts while a search is still ingesting);
  a shared cache of provider responses (would misstate each search's recorded retrieval date — a provenance problem).
  Existing safe caches stay: settings, and versioned static assets (Next.js, immutable).
- **Paper lists, the screening queue, the evidence table and the audit log are already paginated** (offset or keyset);
  the last page of 3,000 papers costs 21 ms vs 14 ms for page 1, so keyset paging is not needed at this scale.

## Targets for production (alert when exceeded; `docs/runbooks/deployment.md#monitoring`)
| Metric | Target | Basis |
|---|---|---|
| API p95 latency, read endpoints | ≤ 1.0 s | met under the 8-user single-process stress above (worst p95 0.82 s); production adds network/TLS |
| API p95 latency, writes (decisions, edits) | ≤ 0.5 s | 0.25 s measured |
| Report generation p95 | ≤ 2 s | 0.69 s measured (small review) |
| Queue delay p95, interactive jobs | ≤ 60 s | depends on worker count; size `WORKER_INTERACTIVE_CONCURRENCY` ≥ concurrent searchers |
| Search of 3,000 records | ≤ 3 min | 16 s alone, 2 min with 8 concurrent searches in one process |
| AI calls in flight | ≤ `LLM_GLOBAL_MAX_CONCURRENT_CALLS` | held exactly (3/3) |
| Job failure ratio | < 5 % | 0 % in the run |

## Capacity guidance
One worker slot runs one job. With the measured run times: a heavy slot parses ≈12 five-page PDFs per minute; an
interactive slot completes a 3,000-record search in ≈16 s of its own time (provider spacing is shared, so more parallel
searches mainly wait on the providers' published rate limits, not on CPU). Set `JOB_MAX_INFLIGHT_GLOBAL` to about twice
the total worker concurrency so workers never idle, and `JOB_MAX_INFLIGHT_PER_USER` so one user cannot take more than
roughly half the slots. Keep `LLM_GLOBAL_MAX_CONCURRENT_CALLS` and `LLM_GLOBAL_DAILY_TOKEN_BUDGET` within the AI
provider's documented allowance for your account tier.

## Not measured here (stated, not assumed)
Real provider latency and quotas; multi-host deployments; network/TLS overhead; reviews larger than 3,000 papers per
project or 8 concurrent active users; real-broker throughput (exercised for correctness in CI, not benchmarked).
