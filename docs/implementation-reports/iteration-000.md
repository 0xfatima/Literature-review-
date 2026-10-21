# Iteration 000 — Phase 0: Audit and architecture

## Summary
Goal: understand the project and fix the architecture before coding. Status: **documentation deliverables complete; zero application code written, by design.** Open blocking question: Q1 (test environment).

## Requirements addressed
Spec §5, §6, §8, §14, §15, §17 (planning only). User roadmap Phase 0 tasks 1–5.

## Files changed
- `docs/architecture/ADR-0001-initial-architecture.md`, `repository-dependency-audit.md`, `erd.md`, `environment-configuration.md`, `implementation-plan.md`
- `docs/requirements/` (original `.docx` + pandoc conversion `requirements.md`)
- `.env.example` (placeholders only), `.gitignore`, `README.md`, `scripts/check_erd.py`
- Folder skeleton (65 dirs, `.gitkeep` only), matching spec §14 with additions (`providers/email`, `providers/auth`, `jobs/`, `evals/`).

## Architecture decisions
See ADR-0001 D1–D13. Notable departures from the spec: sync core (D2); Postgres `jobs` as truth over Redis (D4); self-managed auth (D7); extra tables (§4).

## Tests
| Command | Result |
|---|---|
| `python3 scripts/check_erd.py` | PASS — 30 entities, 44 relationships, 0 hard errors (first run found 1 real gap: decision↔criterion link; fixed, re-run clean) |
| Application tests | **None exist. Not run.** |
| Mermaid render | **Not done** (no renderer offline). |
| Version/provider fact verification | **Not done** (no network). |

## Known issues / limitations
- R1: sandbox has no network/Docker/Postgres/Redis → most later tests can't run here unless Q1 is resolved.
- All dependency versions unverified; Gemini model/quota/terms unverified.
- PyMuPDF licence suitability unconfirmed.
- Deletion/retention policy undecided (scheduled Phase 1).
- ERD constraints (CHECKs, partial uniques) are design intent, not yet validated against a real Postgres.

## Manual verification
Read the five docs in `docs/architecture/`; confirm Q1–Q4 in ADR §8; `python3 scripts/check_erd.py`.

## Next iteration
Phase 1 (M0): auth + projects + foundations — after Q1 is answered.
