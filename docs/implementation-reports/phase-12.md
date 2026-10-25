# Phase 12 report: workflow connected to the IDE-style workspace (2026-10-25)

## Definition of done
"Every major review stage is usable through the workspace, state remains consistent between the chat and panels,
and the interface does not rely on fake progress or demo-only actions." Implemented; pass/fail of the automated
suites is decided by CI (see "Tests").

## What was built
**Persistent review-stage navigator** (`StageNavigator`, backend `GET /workspace/overview`): protocol, search,
screening, full text, extraction, appraisal, synthesis and report, each with a status computed from stored data
(not started / in progress / done / needs attention, spelled out — never colour alone) and a one-line detail
(e.g. "12 of 40 screened · 5 included", "Draft v3 · 2 error(s) · not publication-ready"). Clicking a stage opens its
panel; the current stage is marked with `aria-current`.

**Context that survives switching** — visited workspace tabs stay mounted (hidden), so unsaved edits, filters,
expanded rows and scroll position survive tab switches; the open paper and its inspector tab live in the URL
(`paper`, `ptab`) and survive reloads; chat drafts are kept per thread in session storage and restored when you
come back to the thread or reload; an unsaved-changes registry (chat draft, report edits, appraisal forms, evidence
edits, inspector values) shows "Unsaved edits" in the status bar and warns before the page is closed or reloaded.

**Paper inspector** (side panel on wide screens, full-screen on narrow ones; Esc closes; arrow keys move between its
tabs): Metadata, Abstract, PDF content (page-by-page stored text with OCR warnings and highlighted passages, plus
upload), Evidence (this paper's extraction values) and Screening (decision buttons, history, explanation). Opened
from Papers, Screening cards, the evidence table and chat answers.

**Contextual actions, each a real permission-checked backend operation:**
- *Explain this exclusion* — `POST /papers/{id}/explain-exclusion`: the recorded decision, its reason and the
  protocol criterion it refers to, the reviewer's note, protocol-version currency and any advisory AI assessment,
  as plain sentences; optionally posted into the open chat thread (labelled "built from the review record, no AI").
- *Find supporting evidence* — `POST /papers/{id}/find-evidence`: ranked passages from the paper's stored full
  text for a field or query, copied verbatim with page numbers; "View on the page" highlights it; "Save as cited
  value" stores it through the existing verbatim-checked evidence endpoint. "No match" explicitly says it is not
  evidence of absence.
- *Reassess relevance* — the relevance job now accepts `paper_ids` and re-assesses only those papers.
- *Extract this paper with AI* — the existing extraction job, limited to the paper (consent gates still apply).
- *Ask the assistant* — `POST /conversations/{id}/ask`: a short AI answer grounded only in the protocol, the current
  workflow step and (if open) the inspected paper's bibliographic record; uploaded documents are never sent
  (data class `public_metadata`). Stored as an advisory assistant message with a `model_runs` record; rate-limited
  (`RATE_LIMIT_ASSISTANT_PER_HOUR`); disabled with an explanation when no model is configured.

**Chat ↔ panels:** approval prompts in the chat (`ApprovalPrompt`, used by the protocol question card) gain "Ask a
question about this", which prefills the composer; assistant results carry labels and an "Open paper" link; when
any background job finishes, every query of the project is refreshed so chat, navigator and panels agree.

**Jobs, errors, notifications, recovery:** the status bar keeps live progress for active jobs and adds a Jobs
menu with the last 15 jobs in five distinct states (queued — indeterminate bar, never a fake percentage — running,
completed, failed with its error, cancelled), notifications when a job finishes or fails, and a failed-job count.
`describeError` turns failures into understandable messages (session ended, permission, network, server).

**Reusable components:** `PaperCard`, `DecisionButtons` (+ `ExcludeForm`, now shared by screening cards, bulk
exclude and the inspector), `ExtractionTable`, `ProgressIndicator`, `ApprovalPrompt`, `DocumentPreview`.

## Design decisions
| Decision | Alternative rejected | Why |
|---|---|---|
| Keep visited panels mounted | Persist every panel's state to storage | Preserves all in-progress UI state with no per-panel code |
| Paper selection in the URL | In-memory selection | Survives reloads, shareable within the session, back button works |
| Explanations and evidence search without AI | LLM explanations | Accurate by construction; AI stays for advisory answers only |
| Stage status from one backend endpoint | Derive in the browser from many endpoints | One consistent source; cheaper; testable |
| Synchronous assistant answer | Background job | One short request with the provider timeout; result appears in chat immediately |

## Tests
Added: `tests/unit/test_assist_logic.py` (4, stdlib-only), `tests/integration/test_workspace_assist.py` (5:
explanation + chat post, verbatim evidence search saved as evidence, single-paper relevance reassessment, assistant
answer with fake model / 409 without one, stage overview), `src/lib/workspace.test.ts` (Vitest, 4),
`tests/e2e/workspace-integration.spec.ts` (navigator, draft preserved across tabs and reload, unsaved indicator,
assistant disabled without a model, jobs menu). The existing authorization matrix covers the new routes (no new
path parameters). Run while authoring: ruff format/check, mypy strict (171 files, no issues), `tsc --noEmit`,
`next lint`, Prettier, `validate_static.py`, `check_erd.py`. Not run: pytest, Vitest, `next build`, Playwright.

## Known limitations
- Hidden panels keep polling their queries while mounted (bounded by the number of visited tabs).
- The assistant answers from protocol/workflow/paper metadata only; it does not read uploaded PDFs or evidence.
- Session-storage drafts are per browser tab; closing the tab warns but cannot keep them.
- No global keyboard shortcuts beyond tab/inspector arrow keys, Esc and the existing I/U/E screening keys.

## Migrations / configuration / deployment
No migration. New setting `RATE_LIMIT_ASSISTANT_PER_HOUR` (default 30). New routes: `GET /workspace/overview`,
`GET /assist/status`, `POST /papers/{id}/explain-exclusion`, `POST /papers/{id}/find-evidence`,
`POST /conversations/{id}/ask`; `POST /screening/relevance` accepts `paper_ids`.

## Manual verification
1. Open a project: the stage navigator shows each stage's status; click "Screening" → the Screening tab opens.
2. Type in the chat, switch tabs and reload: the draft is still there and the status bar says "Unsaved edits".
3. Exclude a paper with a criterion reason → "Explain this exclusion": the sentence names the criterion and your
   note; "Explain in the chat" posts it to the thread with an "Open paper" link.
4. Inspect an included paper with a PDF → Evidence → Find supporting evidence for a field → View on the page
   (highlighted) → enter a value → Save as cited value; the evidence table shows it with the page.
5. Start any job and open "Jobs": states change from queued to running to completed; failures show their error.

## Recommended next phase
Hardening and release (audit-log viewer, backup/restore and retention, security/load testing, accessibility
audit with assistive technology, deployment runbook).
