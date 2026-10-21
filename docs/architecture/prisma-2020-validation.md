# PRISMA 2020 validation of the record-status event model (ADR amendment A6)

**Status: design review complete; NOT verified against the live PRISMA sources.**
This sandbox has no network. The flow-diagram structure below is reproduced from my knowledge of the PRISMA 2020 statement (Page et al., *BMJ* 2021;372:n71) and its flow-diagram template. **Before Phase 6 sign-off, someone must compare this document with the official statement and the flow-diagram template at the PRISMA website and record the check date here.** Nothing in this file should be quoted as official PRISMA text.

Verification record: _checked against official source by: ________ on: ________ (blank until done)_

## 1. What the PRISMA 2020 flow diagram reports (as understood)

Two parallel pathways merge into the "included" boxes:

**A. Identification via databases and registers**
1. Records identified from: databases (n), registers (n), reported **per source**.
2. Records removed *before screening*, in **three separate categories**: duplicates; records marked ineligible by automation tools; records removed for other reasons.
3. Records screened (n) → records excluded (n).
4. Reports sought for retrieval (n) → reports not retrieved (n).
5. Reports assessed for eligibility (n) → reports excluded, **with reasons and a count per reason**.

**B. Identification via other methods** (websites, organisations, citation searching, …)
6. Records identified from each such source; then reports sought → not retrieved → assessed → excluded with reasons.

**Merge**
7. Studies included in review (n) **and** reports of included studies (n).

## 2. Findings: gaps in the Phase-0 model, and corrections

| # | Gap in Phase-0 design (ADR D8 / `record_status_events`) | Why it matters | Correction |
|---|---|---|---|
| G1 | No distinction between **records, reports, and studies**. One `paper_records` row was implicitly all three. | PRISMA 2020 reports *reports* through retrieval/eligibility but *studies* at inclusion; several reports can describe one study (e.g. preprint + journal article + trial registry entry), so "studies included" can be smaller than "reports of included studies". | Add `studies` and `paper_records.study_id`. Until a user groups reports, the UI states the assumption "1 report = 1 study", and the diagram labels both numbers. |
| G2 | "Removed before screening" had only *duplicate*. | Three categories are reported. | `reason_code` ∈ `duplicate`, `automation_ineligible`, `other`. |
| G3 | Single pathway only. | Citation searching / web / manual additions form Pathway B with its own boxes. | `pathway` ∈ `A`, `B` on events; `source_tag` ∈ `database`, `register`, `website`, `organisation`, `citation_search`, `manual`. Pathway-B items enter at `sought` by default (configurable to screen at title/abstract). |
| G4 | "Identified" would have been counted from deduplicated `paper_records`. | PRISMA counts identification **before** dedup, per source. | `identified` is computed from `paper_source_records` grouped by provider/source. |
| G5 | No treatment of in-progress work. | A diagram with undecided records can't satisfy the arithmetic. | Explicit `pending` buckets per stage; the flow is **final** only when pending = 0 **and** no AI "uncertain" item lacks a human decision. Otherwise render "in progress" and do not label it PRISMA-complete. |
| G6 | "Automation" removal could have been fed by AI relevance scores. | Conflicts with our rule *no auto-exclusion*, and PRISMA wants automation-removals reported. | `automation_ineligible` is allowed **only** for deterministic, logged rules (e.g. retracted flag, non-matching document type per protocol) — never model scores. Default 0. |
| G7 | Reports "not retrieved" vs "pending upload" were not separated. | Paywalled/unavailable ≠ excluded; pending ≠ not retrieved. | `sought` splits into `awaiting_retrieval` (pending), `not_retrieved` (user-confirmed unobtainable), `assessed_ft`. Only user-confirmed `not_retrieved` is counted as such. |
| G8 | Full-text exclusion reasons were free text. | Per-reason counts require a controlled list. | `reason_code` references protocol criterion `stable_key`s (or a fixed "other: …" bucket). |

## 3. Corrected state machine (per record, per protocol version)

```
Pathway A:  identified ─┬─> removed_before_screening{duplicate|automation_ineligible|other}
                        └─> ta_pending ─> ta_included ─> awaiting_retrieval ─┬─> not_retrieved
                                     └──> excluded_ta                        └─> ft_pending ─┬─> excluded_ft{reason}
Pathway B:  identified_b ─────────────────────────────> awaiting_retrieval ...               └─> included
```
Transitions outside this graph are rejected in code (not merely hidden in the UI). "AI uncertain" is **not** a state: it is a proposal on a record that stays `ta_pending`/`ft_pending` until a human decides.

## 4. Reconciliation invariants (all computed per protocol version)

Let I_A = Σ identified per A-source, R = removed before screening (dup + auto + other), S = screened, X_TA = excluded at title/abstract, K_A = sent to retrieval from A, I_B = identified via B sources, K = K_A + K_B, N = not retrieved, E = assessed, X_FT = Σ per-reason exclusions, M = included reports, T = included studies.

| ID | Invariant | Applies when |
|---|---|---|
| V1 | I_A = Σ_sources identified_source | always |
| V2 | S + R + pending_before_screening = I_A | always |
| V3 | S = X_TA + K_A(from TA) + pending_TA | always |
| V4 | K = K_A + K_B, where K_B = I_B − (B items removed as duplicates of A) | always |
| V5 | K = N + E + pending_retrieval | always |
| V6 | E = X_FT + M + pending_FT | always |
| V7 | X_FT = Σ_reasons count_reason (each reason count ≥ 0; every excluded report has exactly one reason) | always |
| V8 | 1 ≤ T ≤ M when M ≥ 1; every included report belongs to exactly one study | always |
| V9 | No record is in two buckets or two pathways at once; no negative counts; all transitions legal | always |
| V10 | All counts reference a single protocol version (or the diagram states which versions it spans); a criteria change marks affected records `stale` and the diagram `needs_rerun` | always |
| V11 | pending_* = 0 and no unresolved AI-uncertain proposals | **only for a "final" diagram** |

The renderer **refuses** to draw a final diagram if any invariant fails, and shows which one (spec: warnings visible, never hidden). Counts are derived by folding `record_status_events`; they are never typed in by a person or produced by a model.

## 5. Test plan (Phase 6; none written yet)
- Property tests: random legal event streams ⇒ V1–V10 hold; random illegal transitions ⇒ rejected.
- Mutation tests: corrupt one count ⇒ the specific invariant fails.
- Scenario tests: duplicate across A and B; report-of-same-study grouping (T < M); in-progress vs final; protocol revision makes earlier decisions stale.
- Golden test: reproduce an example flow from the official template **only after** the numbers are copied from the official source by a human; no invented numbers.
- Related guidance to review at the same time: PRISMA-S (search-reporting extension) for what `search_runs` must log — **unverified from here**.
