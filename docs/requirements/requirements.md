**Autonomous Systematic Review Scientist**

Version 1.0 \| 19 October 2026 \| Build-ready specification for an AI
coding agent and engineering team

Purpose: specify an end-to-end, human-in-the-loop research application
that discovers papers, screens them, extracts evidence, synthesizes
findings, drafts a literature review, and preserves a reproducible audit
trail.

| **Decision area**      | **Baseline decision**                                                                      |
|------------------------|--------------------------------------------------------------------------------------------|
| Workflow orchestration | LangGraph: explicit state, branching, loops, checkpoints and human interrupts              |
| Backend                | Python + FastAPI, organized as a modular monolith                                          |
| Frontend               | Next.js + TypeScript, dark VS Code/Cursor-inspired research workspace                      |
| Database               | PostgreSQL + SQLAlchemy + Alembic migrations                                               |
| LLM                    | Google Gemini API free tier for prototype; model/provider adapter prevents lock-in         |
| Scholarly discovery    | Official scholarly APIs first; supplementary supported web-search API only when requested  |
| Background work        | Redis-backed worker queue; long tasks never block normal HTTP requests                     |
| Documents              | PyMuPDF for PDFs; local storage in development, S3-compatible object storage in deployment |
| Testing                | Unit, integration, workflow, UI, security, evaluation and load tests                       |
| Operating principle    | AI proposes; deterministic code validates; humans approve consequential research decisions |

Scientific note: PRISMA is a reporting guideline, not a search engine or
a complete review methodology. The product must implement a transparent
selection workflow and generate applicable PRISMA 2020 reporting
artifacts from recorded events. It must not claim that a review is
scientifically complete merely because a flow diagram exists.

# 1. Product vision and goals

The application should feel like a research-focused IDE. A persistent
chat panel sits on the left; the main workspace on the right displays
the active research artifact—search results, paper cards, screening
queue, PDF reader, evidence matrix, comparison tables, synthesis, draft,
PRISMA view, or audit timeline. It should borrow IDE interaction
patterns without pretending research documents are code.

## 1.1 Goals

- Turn a topic, title, or research question into a structured, editable
  review protocol.

- Search suitable scholarly sources with reproducible queries and
  preserved search logs.

- Prioritize openly accessible papers, then optionally discover
  additional records that may require institutional access.

- Rank records against the approved question and criteria, explain
  recommendations, and avoid automatic exclusion based only on AI
  scores.

- Allow PDF uploads, transparent inclusion/exclusion decisions,
  configurable extraction fields, evidence tables, synthesis, and an
  editable literature-review draft.

- Preserve source provenance, user decisions, workflow versions, model
  runs, and audit history.

- Support accounts, private projects, saved chats, resumable work,
  concurrent requests, and incremental deployment.

- Make every feature modular, typed, documented, and tested.

## 1.2 First-release non-goals

- Do not promise publication-ready results without researcher review.

- Do not bypass publisher paywalls, institutional access controls,
  CAPTCHA, or provider terms.

- Do not assume metadata APIs provide full text or that free metadata
  means a paper is open access.

- Do not automatically perform meta-analysis for every field; that
  requires compatible outcomes and validated methods.

- Do not start with microservices, Kubernetes, or one deployment per
  agent. Begin with a modular monolith and separate background workers.

# 2. User journey: end to end

1.  The user registers/signs in, creates a project, and enters a topic,
    title, or initial question in chat.

2.  The Protocol Planner asks only necessary clarifying questions:
    discipline, review type, scope, date range, language, and known
    constraints.

3.  The system proposes a question framework—PICO, PECO, SPIDER, another
    suitable framework, or not applicable—and explains why.

4.  The system proposes research questions, inclusion/exclusion
    criteria, databases/sources, and a search strategy. The user edits
    or approves the protocol before the main search.

5.  The Search workflow calls configured scholarly APIs, saves exact
    queries and run metadata, normalizes results, and deduplicates
    records.

6.  The workspace shows records with metadata, abstract, source links,
    access status, estimated relevance, rationale, and selection
    controls.

7.  The user is asked whether to run supplementary discovery for
    additional papers that may require institutional access. If yes, a
    supported web-search provider finds metadata/public abstracts; the
    user obtains lawful access and uploads PDFs.

8.  The relevance stage ranks abstracts against the approved protocol.
    It uses explicit labels and explanations, not unexplained colors or
    an uncalibrated probability claim.

9.  The user marks records Include, Exclude, or Unsure. Each decision is
    persisted and auditable.

10. The user uploads available PDFs. The system validates files,
    extracts page-aware text, matches PDFs to records, and asks for
    confirmation when a match is uncertain.

11. Title/abstract screening and full-text screening remain separate
    stages. Full-text exclusions require a reason.

12. The user confirms final research questions. The system maps
    questions to included studies and reports apparent evidence coverage
    and gaps, without claiming certainty from abstracts alone.

13. Before extraction, the user can choose fields using selectable
    chips/bubbles, add custom fields, or skip and use a recommended
    default schema.

14. The Evidence Extractor creates structured evidence with source
    passages and page/section references. Missing information is
    represented explicitly rather than guessed.

15. The user reviews/edit extraction results. Uncertain fields are
    flagged and can be edited or rerun individually.

16. Where appropriate, the system assists with critical
    appraisal/risk-of-bias assessment using a suitable framework; the
    human verifies judgments.

17. The Synthesis stage builds comparison tables, themes,
    contradictions, limitations, and evidence gaps from approved
    evidence.

18. The Writer creates an editable literature-review draft with
    citations tied to verified records and evidence.

19. The Audit/Verification stage checks counts, citation targets, source
    links, missing fields, and reporting completeness.

20. The user reviews, edits, exports, and later reopens the project with
    chat, decisions, artifacts, and workflow state intact.

# 3. Functional requirements

## 3.1 Accounts, projects and saved chat

- Provide registration, sign-in, sign-out, and password reset or a
  supported authentication-provider flow.

- Prefer a maintained auth provider/library. If managing passwords, use
  Argon2id or bcrypt, secure sessions, and established reset-token
  practices.

- Users can create, rename, archive, reopen, and delete their own
  projects.

- Persist topic, protocol versions, research questions, criteria, search
  runs, paper records, PDFs, screening decisions, extraction
  schema/results, appraisal, synthesis, draft versions, audit events,
  workflow checkpoints, and chat history.

- Chats are project-specific and resumable. Important decisions must
  also be saved as typed database records; the transcript alone is not
  the source of truth.

- Every project-scoped API verifies ownership server-side. Hiding
  another user's project in the UI is not authorization.

- Show project statuses such as Setup, Searching, Screening, Awaiting
  PDFs, Extracting, Synthesizing, Draft Ready, Needs Attention, and
  Completed.

## 3.2 Topic intake and protocol

- Accept a plain-language topic, working title, or research question;
  preserve the original text if the AI suggests a rewrite.

- Ask only the clarifying questions needed to make the protocol usable.
  Allow partial answers and save for later.

- Suggest PICO, PECO, SPIDER, or another framework based on the question
  and discipline; support Not Applicable/Other.

- Propose inclusion and exclusion criteria individually with rationale.
  Users can add, edit, remove, accept, or reject each criterion.

- Protocol fields should include objective, questions, framework,
  criteria, date range, language, study designs, sources, search
  strategy, extraction plan, and synthesis plan where known.

- Require human approval before the primary search/screening run. Record
  approver, timestamp, and exact protocol version.

- If criteria/questions change after screening starts, create a new
  protocol version and show which results may require re-screening.

## 3.3 Scholarly search and supplementary web discovery

| **Source / component**     | **Purpose**                                           | **Requirements and caveats**                                                               |
|----------------------------|-------------------------------------------------------|--------------------------------------------------------------------------------------------|
| OpenAlex API               | Broad scholarly discovery and metadata                | Save query, filters, pagination, timestamps and returned counts; obey current rate limits. |
| Crossref REST API          | DOI/publication metadata and matching                 | Metadata resolution, not a full-text guarantee.                                            |
| PubMed / Europe PMC        | Biomedical/life-science literature                    | Use when relevant to discipline; preserve source-specific query syntax.                    |
| arXiv API                  | CS, AI, physics and related preprints                 | Clearly label preprints; do not imply peer review.                                         |
| Other official domain APIs | Discipline-specific coverage                          | Enable only when source fits the domain and terms permit intended use.                     |
| Supported web-search API   | Supplementary discovery of publisher/repository pages | Use only when user opts in; do not make scraping Google Scholar the default.               |
| User PDF uploads           | Full texts the user can lawfully access               | Secure storage, matching, parsing, provenance and user confirmation when uncertain.        |

- Prioritize records with verified open-access/full-text indicators when
  available. Distinguish open metadata from open full text.

- After the initial search, ask whether the user wants supplementary
  discovery for papers that may require institutional access. Choices:
  Yes, No, Later.

- If yes, search public web results and metadata. The application must
  not circumvent paywalls; it asks the user to obtain and upload
  accessible PDFs.

- Each search run records source/provider, exact query, filters,
  timestamp, pagination/cursor, counts, failures, and limitations.

- Support retry/resume without duplicate records. Partial provider
  failures must remain visible and must not be reported as a complete
  search.

- Allow query edits, source additions, and reruns. Preserve prior runs
  rather than overwriting them.

- Use source adapters behind a common interface so new scholarly
  providers can be added without rewriting workflow logic.

## 3.4 Abstract relevance and paper cards

| **UI signal**  | **Meaning**                              | **Required behavior**                                               |
|----------------|------------------------------------------|---------------------------------------------------------------------|
| Green          | High estimated relevance                 | Show 'High relevance', icon, rationale and human decision controls. |
| Lime           | Moderate-to-high relevance               | Show 'Potentially relevant' and any uncertainty.                    |
| Light green    | Possible relevance / insufficient detail | Show 'Needs review'; do not auto-exclude.                           |
| Grey           | Not assessed / metadata insufficient     | Do not treat as low relevance.                                      |
| Decision badge | Included / Excluded / Unsure / Pending   | Use text and icon as well as color.                                 |

- Relevance must be estimated from the approved question, abstract,
  keywords, and criteria. Explain which concepts/criteria matched or
  were unclear.

- Do not label a score as a probability unless calibrated on a labeled
  evaluation set. Prefer 'estimated relevance'.

- Each card shows title, authors, year, venue/source, DOI/link, abstract
  snippet, access status, relevance label, rationale, and
  Include/Exclude/Unsure/Open/Upload controls.

- Provide filters for relevance, year, source, access, decision, and
  screening stage; support bulk actions with confirmation.

- Missing abstract means 'not assessed/insufficient metadata', not
  irrelevant.

- Abstract assessment must not be confused with full-text assessment.

## 3.5 PDF upload and parsing

- Provide drag-and-drop and file-picker upload with progress, status,
  size, and actionable errors.

- Validate extension, MIME type, file signature, size limits, and safe
  filenames; treat documents as untrusted input.

- Use a storage adapter: local filesystem for development and
  S3-compatible object storage in deployed environments.

- Use PyMuPDF for text extraction. Add OCR for scanned pages only when
  needed because OCR increases cost and may be less accurate.

- Match PDFs using DOI, title, authors, year and fuzzy matching. Ask the
  user to confirm uncertain matches.

- Track checksum, original filename, uploader, page count, parse status,
  document version, and extracted-text version.

- Handle corrupt, encrypted, empty, oversized and unsupported files with
  clear errors and retry options.

- Send only relevant sections/chunks to an LLM when possible; do not
  repeatedly submit entire papers.

## 3.6 Screening and PRISMA

PRISMA 2020 is a reporting guideline. The application should maintain
transparent screening states and generate reporting counts from actual
persisted events—not from values generated by an LLM.

- Maintain separate states for identified, deduplicated, title/abstract
  screened, excluded at title/abstract, sought for retrieval, not
  retrieved, full-text assessed, excluded at full text with reason, and
  included.

- Store record counts as derived values from database records/events. Do
  not let the LLM invent or manually type final counts.

- AI screening returns Include / Exclude / Uncertain, criterion IDs,
  evidence excerpt, concise rationale, uncertainty, model ID, and prompt
  version.

- Keep uncertain cases for human review. Human decisions and overrides
  are first-class records.

- At full-text stage, require a primary exclusion reason and allow
  notes.

- Account separately for database searching, supplementary web search,
  manual additions, citation chasing, and other sources where relevant.

- Generate PRISMA diagrams only after validating count transitions and
  identifying missing stages. Show warnings for inconsistent counts.

- Offer the official PRISMA 2020 checklist/reporting aid where
  applicable, without implying that the checklist certifies
  methodological quality.

Reference: https://www.prisma-statement.org/prisma-2020. The
user-provided AD Publications guide can be used as a secondary
explanation, but official PRISMA materials and discipline-specific
methods take precedence.

## 3.7 Research-question coverage

- Let users confirm/edit final research questions after screening.

- Map each question to included papers and extracted evidence. Display
  Answered, Partially addressed, Not found in included evidence, or Not
  assessed as workflow indicators.

- Show supporting paper IDs, evidence excerpts and missing-information
  notes.

- Do not claim a question is definitively answered from abstracts alone.

- If a question changes materially, warn about protocol/search/screening
  impact, version the protocol, and identify downstream artifacts that
  may be stale.

## 3.8 Optional extraction-field bubbles

Before extraction, offer selectable chips/bubbles. The step is optional;
users may skip it and accept a recommended default schema.

| **Field**                    | **What it captures**                                              |
|------------------------------|-------------------------------------------------------------------|
| Citation details             | Title, authors, year, venue, DOI                                  |
| Objective                    | Study aim/question                                                |
| Design/method                | Experiment, survey, qualitative study, benchmark, etc.            |
| Population/context           | Participants, domain, setting, application                        |
| Dataset/sample               | Dataset name, sample size, split, collection details              |
| Intervention/model/technique | Method, architecture, intervention or exposure                    |
| Comparator/baseline          | What the study compares against                                   |
| Metrics/outcomes             | Metrics and definitions used                                      |
| Main findings                | Reported results with units, conditions and caveats               |
| Limitations                  | Author-reported and reviewer-identified limitations kept distinct |
| Quality/risk of bias         | Only when a suitable framework applies                            |
| Future work                  | Research directions stated by the authors                         |
| Evidence location            | Short supporting excerpt, page, section, table/figure             |
| Custom fields                | User-defined fields with types and validation rules               |

- Each field has a key, display name, description, data type,
  required/optional flag, validation, and applicability rules.

- Allow add/remove/reorder/rename before extraction. Schema changes
  after extraction are versioned and can trigger targeted re-extraction.

- Use explicit states such as Not reported, Unclear, Not applicable, and
  Not assessed. Never fill missing values with plausible guesses.

## 3.9 Evidence extraction, synthesis and writing

- Extract schema-bound values per paper and validate output with
  Pydantic or equivalent.

- Store supporting excerpts and page/section locations for important
  extracted values; preserve the original source document.

- Provide editable comparison/evidence tables with sorting, filtering,
  search, column visibility, and CSV/XLSX export.

- Generate synthesis themes, comparisons, contradictions, limitations
  and evidence gaps from included studies and approved evidence.

- Clearly distinguish reported facts, synthesis/interpretation, and
  uncertainty. Do not convert correlation into causation or overstate
  findings.

- Meta-analysis is out of MVP scope unless selected later and supported
  by compatible data and validated statistical methods.

- Draft sections as appropriate: title, abstract,
  introduction/background, methods, results, discussion, limitations,
  conclusion, references and appendices.

- Every citation resolves to a verified bibliographic record. Important
  claims should link to evidence items and paper IDs, not just free-text
  citation strings.

- Allow editing, section-level regeneration, accept/reject suggested
  changes, draft version comparison, and DOCX export. Markdown/PDF can
  follow.

- Verify citations, source targets, and missing evidence before export;
  do not silently rewrite scientific claims during verification.

# 4. Human-in-the-loop checkpoints

| **Checkpoint**      | **User sees**                                | **Actions**                                       | **Workflow response**                                                |
|---------------------|----------------------------------------------|---------------------------------------------------|----------------------------------------------------------------------|
| Protocol            | Question, framework, criteria, search plan   | Approve / Edit / Pause / Ask                      | Save approved immutable version for the run.                         |
| Search completion   | Sources, exact queries, counts, failures     | Continue / Retry source / Add source / Edit query | Use selected, documented result set.                                 |
| Relevance ranking   | Cards, abstracts, rationale, access status   | Include / Exclude / Unsure                        | Persist decisions and keep uncertain items visible.                  |
| Criteria revision   | Current criteria and impact preview          | Edit / Approve new version                        | Mark affected screening outputs stale and rerun only what is needed. |
| Full-text screening | Paper text, criteria, AI recommendation      | Include / Exclude with reason / Unsure / Override | Persist decision, reviewer and reason.                               |
| Question coverage   | Question-to-evidence map                     | Accept / Edit / Mark gap                          | Warn if changes affect protocol or search.                           |
| Extraction schema   | Field bubbles and default schema             | Choose / Customize / Skip                         | Version schema before extraction.                                    |
| Extraction review   | Values, quotes, page references, uncertainty | Accept / Edit / Mark unclear / Rerun field        | Update evidence matrix and audit.                                    |
| Synthesis review    | Themes, comparisons, gaps                    | Approve / Edit / Reorganize                       | Writer uses approved synthesis version.                              |
| Final review        | Draft, citation checks, audit report         | Edit / Export / Return to prior stage             | Record final reviewed/exported version.                              |

Every checkpoint explains why input is needed, what approval does,
whether the action is reversible, and which downstream artifacts might
be rerun. Save checkpoints so the user can leave and resume later.

# 5. Agentic architecture

## 5.1 Recommendation

Use LangGraph to orchestrate a stateful graph. Implement agents as
logical responsibilities—graph nodes or reusable services—not as
separate servers. A modular monolith is simpler to test and deploy;
workers handle slow tasks.

| **Logical agent/module**    | **Responsibilities**                                                                | **LLM use**                                           | **Human involvement**                              |
|-----------------------------|-------------------------------------------------------------------------------------|-------------------------------------------------------|----------------------------------------------------|
| 1\. Protocol Planner        | Clarify scope; suggest framework, questions, criteria, fields and search plan       | Yes                                                   | Approve protocol                                   |
| 2\. Search Coordinator      | Build source-specific queries, call scholarly adapters, summarize coverage/failures | LLM may formulate queries; APIs retrieve records      | Approve search plan and inspect results            |
| 3\. Relevance & Screening   | Rank abstracts; classify against criteria; explain uncertainty; assess full text    | Yes                                                   | Review uncertain cases and approve final selection |
| 4\. Evidence Extractor      | Extract schema-bound fields and provenance                                          | Yes                                                   | Review/edit extraction                             |
| 5\. Critical Appraisal      | Assist with relevant quality/risk-of-bias framework                                 | Assistive only                                        | Human verifies judgments                           |
| 6\. Evidence Synthesizer    | Compare results, group themes, flag contradictions and gaps                         | Yes, grounded in evidence                             | Approve synthesis                                  |
| 7\. Writer & Audit Reviewer | Draft sections and run evidence/reporting checks                                    | LLM for drafting; deterministic validators for checks | Final review                                       |

These are seven logical responsibilities, not seven independently
deployed agents or seven separate LLMs. For the MVP, group them into
five graph modules: Protocol/Search, Screening, Extraction/Appraisal,
Synthesis/Writing, and Audit/Verification. Split later only when
evaluation justifies it.

## 5.2 State, loops and routing

- Represent stages as named graph nodes with typed input/output schemas.

- Use conditional routing for approved/rejected, clear/uncertain,
  success/retry, validation pass/fail, and user-requested revision.

- Support backtracking. Example: a changed criterion creates a new
  protocol version, identifies affected screening decisions, and reruns
  only impacted tasks.

- Checkpoint before human interruptions and after meaningful stage
  completion. Resume from saved state rather than restarting.

- Bound automatic retries/iterations; record reason, iteration count and
  stop condition to prevent infinite loops.

- Use idempotency keys so retries do not duplicate papers, evidence
  records, or decisions.

- LLMs must not write directly to the database. They return structured
  proposals that validated service functions persist.

- Store large PDFs and extracted documents outside transient graph
  state; graph state should contain IDs and compact metadata.

## 5.3 Typed workflow state

Define ReviewState with project_id, run_id, protocol_version_id,
active_stage, search_run_ids, paper IDs, decisions, selected document
IDs, extraction schema version, evidence result IDs, synthesis version,
draft version, pending human question, approval state, error state,
iteration count and audit correlation ID.

## 5.4 Prompts and model management

- Keep prompts in version-controlled files, not scattered across
  business logic.

- Record provider/model ID, prompt version, settings, token usage where
  available, duration, retries and status for every model run.

- Validate structured output before accepting it; handle invalid output
  as a recoverable error.

- Treat paper/PDF text as untrusted evidence. Instructions inside a
  paper must never override system rules or trigger tools.

- Use one Gemini model at first. Add a separate verifier only after
  evaluation shows measurable improvement.

- Use low randomness for extraction/classification where supported, but
  never treat temperature as a correctness guarantee.

- Wrap the LLM behind a provider interface so model changes do not
  require rewriting workflow nodes.

# 6. Tech stack and rationale

| **Layer**              | **Choice**                                                    | **Why**                                                                           |
|------------------------|---------------------------------------------------------------|-----------------------------------------------------------------------------------|
| Frontend               | Next.js + TypeScript                                          | React ecosystem, routing, typed interfaces and production build.                  |
| UI styling             | Tailwind CSS + reusable component library                     | Consistent dark IDE-inspired design and reusable UI tokens.                       |
| Server data fetching   | TanStack Query or equivalent                                  | Caching, request states, retries and reduced duplicate calls.                     |
| Client UI state        | Zustand or React context where enough                         | Small UI state only; database remains source of truth.                            |
| Backend                | Python + FastAPI                                              | Typed APIs, async I/O, OpenAPI docs and AI/document ecosystem.                    |
| Workflow orchestration | LangGraph                                                     | State, branches, loops, checkpointing, pause/resume and human approval.           |
| Schemas                | Pydantic                                                      | Validate API inputs and structured model outputs.                                 |
| Database               | PostgreSQL                                                    | Relational integrity for users, projects, records, decisions, versions and audit. |
| ORM/migrations         | SQLAlchemy 2.x + Alembic                                      | Testable database access and controlled schema evolution.                         |
| Background jobs        | Redis + Celery or compatible worker                           | Long search, PDF, batch extraction and export jobs run outside HTTP requests.     |
| PDF parsing            | PyMuPDF; OCR only when necessary                              | Fast page-aware text extraction without always paying OCR cost.                   |
| Storage                | Local dev adapter; S3-compatible object storage in deployment | Securely stores PDFs and exports; keeps app portable.                             |
| Scholarly discovery    | OpenAlex, Crossref, PubMed/Europe PMC, arXiv as relevant      | Structured metadata from appropriate sources.                                     |
| Supplementary search   | Supported search API/provider                                 | Additional discovery without depending on brittle or disallowed scraping.         |
| LLM                    | Google Gemini API free tier for prototype                     | Low initial cost; quotas/model availability/data terms must be checked.           |
| Testing                | pytest, HTTPX/TestClient, Playwright, load testing tool       | Backend, workflow, UI and performance coverage.                                   |
| Observability          | Structured logs, metrics, error tracking                      | Debug latency/failures without logging secrets or full private papers.            |
| Deployment             | Docker + managed app hosting/database when possible           | Repeatable, light initial deployment with lower operations burden.                |

Avoid adding Kubernetes, microservices, a vector database, or pgvector
before there is a demonstrated need. This product's core is structured
metadata, evidence provenance, and resumable workflows. Embeddings can
be an optional retrieval/ranking tool, not the source of truth.

## 6.1 Deployment shape

Use one repository. In local development, Docker Compose runs the
frontend, API, PostgreSQL, Redis/worker and local storage. In
production, deploy the frontend, API and workers as separate processes;
use managed PostgreSQL and object storage if available. API instances
should remain stateless so they can scale horizontally.

## 6.2 API principles

- Use versioned endpoints such as /api/v1/projects and
  /api/v1/projects/{id}/runs.

- Use REST for CRUD/job control and Server-Sent Events (SSE) for
  progress/chat streaming if adequate; use WebSockets only if true
  bidirectional realtime is needed.

- Return a run/job ID for long work; the UI subscribes or polls for
  progress.

- Keep API handlers short and enqueue long tasks.

- Use pagination/filtering, standard error objects, correlation IDs, and
  FastAPI-generated OpenAPI documentation.

# 7. UI/UX: dark research IDE

## 7.1 Layout

| **Region**                  | **Contents and behavior**                                                                                                                    |
|-----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| Top bar                     | Product/project name, save status, active run status, settings/profile.                                                                      |
| Activity rail               | Projects, Search, Papers, Screening, Evidence, Synthesis, Draft, Audit, Settings.                                                            |
| Explorer/sidebar            | Project tree and saved artifacts, similar to an IDE file explorer.                                                                           |
| Left chat                   | Conversation, contextual questions, approval cards, progress and quick actions.                                                              |
| Main workspace              | Tabs for Search Results, Paper Details, PDF Reader, Screening Queue, Evidence Matrix, Comparison Tables, Synthesis, Draft, PRISMA and Audit. |
| Optional right detail panel | Selected paper metadata, abstract, criterion mapping, extracted fields and decision history.                                                 |
| Bottom status bar           | Workflow stage, paper counts, pending approvals, background status, last saved time.                                                         |

## 7.2 Layout behavior

- Desktop-first at 1280px+, responsive for laptops/tablets; collapse
  panels into drawers on narrow screens.

- Keep chat on the left and the active research artifact in the main
  area.

- Allow resizing/collapsing chat and explorer panels. Preserve active
  tabs and selection when switching views.

- Show human-readable progress; raw logs belong in an optional details
  drawer.

- Show Saving/Saved/Failed to save and retry failed persistence.

- Use loading skeletons and streamed progress rather than freezing the
  whole UI.

- Provide useful empty states that explain the next action.

- Do not expose hidden chain-of-thought. Show concise rationale,
  sources, decisions, and implementation summaries.

## 7.3 Chat patterns and accessibility

- Prefer contextual questions and quick-reply chips over a giant form
  asking everything at once.

- Approval cards show a summary, consequences and Approve/Edit/Pause
  actions.

- When going back to a prior stage, explain what changes and which
  downstream artifacts become stale.

- Target WCAG 2.2 AA practices where feasible: contrast, keyboard
  navigation, visible focus, labels and semantic structure.

- Never rely on color alone. Relevance colors must also have text labels
  and icons.

- Provide confirmation for destructive actions and reversal/override
  paths for research decisions.

## 7.4 Key screens

| **Screen**        | **Minimum elements**                                                             |
|-------------------|----------------------------------------------------------------------------------|
| Sign in/register  | Validation, errors/loading, reset flow.                                          |
| Project home      | Create/open project, recent work, progress and pending approvals.                |
| Research setup    | Topic, questions, framework suggestion, criteria, date/language/source settings. |
| Search plan       | Source-specific query previews, filters, edit and approve.                       |
| Search results    | Filters, relevance labels, abstract, access/source details, selection controls.  |
| Paper detail      | Metadata, abstract, links, decision history, PDF match/upload state.             |
| Screening queue   | Paper text, criteria checklist, AI rationale, Include/Exclude/Unsure.            |
| PDF workspace     | Page navigation, parsing status, source excerpts, matching controls.             |
| Extraction schema | Selectable field bubbles, custom field editor, preview.                          |
| Evidence matrix   | Editable table, provenance, uncertainty flags, exports.                          |
| Synthesis         | Themes, comparisons, contradictions, evidence gaps.                              |
| Draft editor      | Editable sections, citation navigation, version history, DOCX export.            |
| Audit             | Timeline, filters, search, decision changes, model runs, queries, export.        |

# 8. Data model

| **Entity/table**                    | **Purpose / key fields**                                                                                     |
|-------------------------------------|--------------------------------------------------------------------------------------------------------------|
| users                               | id, email, auth reference/password hash, created_at, status                                                  |
| projects                            | id, owner_id, title, topic, discipline, review_type, status, timestamps                                      |
| conversations / messages            | project/user links, role, content, structured payload, timestamp                                             |
| protocol_versions                   | project_id, version, questions, framework, criteria, filters, source plan, schema version, approver/time     |
| search_runs                         | source, exact query, filters, cursor/page, timestamps, status, result count, error summary                   |
| paper_records                       | canonical DOI, title, abstract, authors, year, venue, metadata, access status, normalized hash               |
| paper_source_records                | paper_id, search_run_id, provider record ID, source URL, raw metadata                                        |
| paper_documents                     | paper_id, uploader, storage key, checksum, size, page count, parse status, text version                      |
| screening_decisions                 | paper_id, stage, protocol version, decision, exclusion reason, rationale, excerpt, model run, reviewer, time |
| extraction_schemas                  | project_id, version, field definitions, created_at                                                           |
| evidence_items                      | paper_id, schema version, field key/value, status, page/section, excerpt, model run, reviewer                |
| appraisal_records                   | paper_id, framework, judgments, evidence, status, reviewer                                                   |
| synthesis_versions / draft_versions | project, version, themes/claims or sections, status, created_at                                              |
| claim_evidence_links                | draft version, claim ID, paper ID, evidence item ID, citation locator                                        |
| workflow_runs / model_runs          | graph version, stage, status, checkpoint, model/provider, prompt version, tokens, duration                   |
| audit_events                        | actor, action, entity, before/after state, reason, timestamp, correlation ID                                 |

- Use UUIDs or similarly non-guessable IDs, foreign keys, uniqueness
  constraints, and indexes for project, status, DOI, and common filters.

- Keep PDFs and large text outside frequently queried project rows.

- Use optimistic concurrency/version numbers for edits to protocols,
  decisions, evidence and drafts.

- Audit events are append-only from the application perspective:
  corrections create new events rather than erasing history.

- Never log passwords, API keys, access tokens, or unnecessary full
  document contents.

# 9. Initial API surface

| **Method + route**                          | **Purpose**                                |
|---------------------------------------------|--------------------------------------------|
| POST /api/v1/auth/register                  | Register or start supported auth flow      |
| POST /api/v1/auth/login                     | Authenticate                               |
| POST /api/v1/projects                       | Create project                             |
| GET /api/v1/projects                        | List owned projects                        |
| GET/PATCH/DELETE /api/v1/projects/{id}      | Read/update/delete authorized project      |
| POST /api/v1/projects/{id}/conversations    | Create/start project chat                  |
| POST /api/v1/projects/{id}/messages         | Send message and possibly advance workflow |
| GET /api/v1/workflow-runs/{id}              | Read status/progress                       |
| POST /api/v1/workflow-runs/{id}/resume      | Resume after user input/approval           |
| POST /api/v1/projects/{id}/protocol/approve | Approve protocol version                   |
| POST /api/v1/projects/{id}/search-runs      | Start search job                           |
| GET /api/v1/projects/{id}/papers            | List/filter papers                         |
| PATCH /api/v1/papers/{id}/decision          | Save screening decision                    |
| POST /api/v1/projects/{id}/documents        | Upload PDF                                 |
| GET /api/v1/projects/{id}/evidence          | Read evidence matrix                       |
| PATCH /api/v1/evidence/{id}                 | Edit/approve evidence field                |
| GET /api/v1/projects/{id}/audit-events      | Read audit trail                           |
| POST /api/v1/projects/{id}/exports          | Start export job                           |

Route names can evolve, but maintain consistent authorization and API
contracts. Search, upload, parsing, batch extraction and exports should
be asynchronous jobs.

# 10. Security, privacy and reliability

- Keep Gemini/API secrets on the backend; never expose them in frontend
  bundles or commit them to source control.

- Use environment variables locally and a managed secret store in
  deployment.

- Use HTTPS, secure HttpOnly SameSite cookies when using cookie
  sessions, CSRF protection as applicable, and a strict CORS allowlist.

- Rate-limit login, uploads, search, and expensive model endpoints.
  Enforce per-user quotas.

- Validate inputs/files; set file-size limits, timeouts, safe names, and
  content-type checks.

- Defend against prompt injection: PDF/abstract content is untrusted
  evidence, not instructions; tool access must be restricted.

- Check project ownership for every project-scoped resource to prevent
  insecure direct object references.

- Use ORM/parameterized queries; never concatenate user input into SQL.

- Apply timeouts, bounded retries, exponential backoff and
  provider-specific rate limits to external APIs.

- Cache public metadata only where provider terms allow; never share
  private document content across users.

- Provide database backups and test restoration before production.

- Define project/document deletion and retention behavior clearly.

- Do not log complete PDFs or private chat by default; log IDs, counts,
  timings, status and sanitized errors.

- Before launch, verify current Gemini model availability, free-tier
  quotas, data-use terms and rate limits. Free-tier access is not
  unlimited and may change.

# 11. Scalability and latency

| **Concern**         | **Design response**                                                                          |
|---------------------|----------------------------------------------------------------------------------------------|
| Long-running tasks  | Queue search, parsing, screening, extraction and exports.                                    |
| Concurrent users    | Async I/O, bounded worker concurrency, DB pool limits and provider rate limits.              |
| Large PDFs          | Background processing, page-aware chunks, targeted excerpts and OCR only when needed.        |
| Repeated work       | Cache public metadata; reuse results only when input/protocol/model/prompt versions match.   |
| Rate limits/outages | Queue, backoff, retry, visible partial results and resumable jobs.                           |
| Database load       | Indexes, pagination, short transactions, connection pooling.                                 |
| UI responsiveness   | Progress stream, job IDs, skeletons, optimistic UI only for reversible actions.              |
| Horizontal scaling  | Scale stateless API and workers independently; shared state in DB/object storage/queue.      |
| Cost control        | Track token usage, cap loops, batch safely, cache, and warn before unusually expensive runs. |

Do not invent a production SLA before benchmarking. Measure p50/p95 API
latency, queue wait, throughput, error rate, model latency, and cost per
completed review. Normal API calls should return promptly; long tasks
return a run ID and continue in the background.

# 12. Testing strategy

Every module must have automated tests. Use mocked LLM/provider
responses for deterministic tests; use a small opt-in evaluation suite
for live API calls.

## 12.1 Unit tests

- Protocol validation, unsupported framework values, criterion edits and
  versioning.

- Source-specific query builders, escaping, date filters, pagination and
  reproducibility.

- Metadata normalization for missing abstracts/authors/DOIs, Unicode and
  malformed records.

- Deduplication by DOI/title/authors/year, preprint versus published
  version, uncertain match and false-merge prevention.

- Relevance labels/thresholds, missing-abstract behavior and rationale
  presence.

- Screening parser: valid/invalid decisions, uncertainty, required
  full-text exclusion reason.

- Upload validation: extension/MIME mismatch,
  oversized/corrupt/encrypted/empty PDFs and checksum.

- PDF parsing, page references, OCR fallback and parser failures.

- Extraction schemas, custom field types, null/Not reported/Unclear/Not
  applicable states.

- Citation resolution, missing source IDs, duplicate references and DOI
  formatting.

- PRISMA transitions/count invariants, not-retrieved records, exclusions
  and impossible totals.

- Audit actor/before-after behavior and secret redaction.

- Authorization: every project-scoped entity is inaccessible to other
  users.

- Idempotency/retries: repeated task submission does not duplicate
  records or decisions.

## 12.2 Integration tests

- API + test PostgreSQL: projects, ownership, protocol versions,
  decisions and audit.

- Mocked search providers: success, empty results, malformed response,
  pagination, timeout, HTTP 429 and 5xx.

- Workflow graph: pause/resume, rejection, return to earlier node,
  bounded retry and crash recovery.

- PDF upload → storage → parsing → matching → user confirmation → page
  text retrieval.

- Extraction output → schema validation → database → user edit → version
  history.

- Draft generation → claim/evidence links → invalid citation rejection.

- DOCX export preserves headings, tables, citations and references.

## 12.3 End-to-end UI tests

- Register/login/logout and protected routes.

- Create project, enter topic, edit criteria, approve protocol and
  resume after reload.

- Run search, filter/select papers and confirm choices persist after
  refresh.

- Opt into supplementary discovery, see access disclaimer, upload PDF
  and confirm uncertain match.

- Screen abstracts/full texts, enter exclusion reasons and see PRISMA
  counts update.

- Choose extraction bubbles or skip; review/edit evidence cells.

- Review synthesis, edit draft, inspect citations, export DOCX and
  reopen project.

- Pause at every human checkpoint, close/reopen browser and resume
  without lost state.

- Verify dark theme, keyboard controls, responsive panels, loading and
  error states.

## 12.4 Load, resilience and AI quality tests

- Load test concurrent API users and background jobs with synthetic
  data.

- Simulate provider rate limits/outages, slow calls, worker/Redis
  restarts and database connection failures.

- Verify partial source failure never appears as a complete search.

- Create a human-labeled abstract set to evaluate relevance ranking and
  screening precision/recall.

- Measure extraction completeness/correctness and source-page/quote
  accuracy against manual annotations.

- Measure draft citation support, unsupported-claim rate, and broken
  citation rate.

- Track human override rates and error patterns; do not use model
  self-evaluation as the sole quality metric.

## 12.5 Definition of done

A feature is not complete until its unit tests pass, relevant
integration/UI tests pass, errors and loading states are covered,
authorization and audit behavior are verified, and the implementation
report states tests run and known gaps. Never claim a test passed unless
it was actually executed.

# 13. Iterative coding-agent workflow and reports

The AI coding assistant must work in small, reviewable iterations. After
every iteration it writes a report in
docs/implementation-reports/iteration-XXX.md.

## 13.1 Required cycle

21. Inspect repository structure and conventions before changing files.

22. State goal, requirement IDs, assumptions, affected modules, and
    acceptance criteria.

23. Implement one cohesive vertical slice; avoid unrelated refactors.

24. Run formatting, linting, type checks, unit tests, and relevant
    integration/UI tests.

25. Review diffs for secrets, unrelated changes, duplicate logic, and
    missing error handling.

26. Update docs, environment example, migrations and API schemas as
    needed.

27. Write the report and update overall status/known-issues
    documentation.

28. Distinguish tests run from tests not run; include exact commands and
    outcomes.

29. When a decision changes, record old choice, new choice, reason,
    impact and migration plan.

## 13.2 Required implementation report

| **Section**            | **Required contents**                                                 |
|------------------------|-----------------------------------------------------------------------|
| Summary                | Goal, status, user-visible outcome                                    |
| Requirements           | Requirement IDs and acceptance criteria addressed                     |
| Files changed          | Every significant file/path and reason                                |
| Architecture decisions | Options considered, choice, rejected alternatives and why             |
| Data/API changes       | Routes, schemas, migrations and compatibility                         |
| AI/workflow            | Node behavior, prompt version, state transitions, loops and approvals |
| Tests                  | Exact commands, pass/fail counts, skipped tests and reasons           |
| Security/reliability   | Authorization, validation, retries, logs and secrets                  |
| Known issues           | Bugs, limitations, assumptions and follow-up work                     |
| Manual verification    | Steps a human can follow                                              |
| Next iteration         | Smallest sensible next milestone                                      |

## 13.3 Code quality rules

- Apply SOLID where useful: one clear responsibility per module,
  dependency inversion for providers/storage, small testable functions.

- Use clear names, type hints, docstrings for non-obvious behavior, and
  comments explaining why.

- Avoid giant files, circular imports, magic values, duplicate provider
  logic, and scattered direct LLM calls.

- Use dependency injection for provider clients and workflow services so
  tests can mock them.

- Use database migrations for schema changes; never silently change a
  production schema.

- Do not delete/rewrite existing work without explaining why.

- Ask the user only when a decision materially affects scientific
  validity, cost, access or product behavior; otherwise make a
  documented reversible assumption.

- Never fabricate papers, citations, API keys, test outcomes or
  deployment status.

# 14. Suggested repository structure

systematic-review-scientist/

apps/web/

src/app/

src/components/{layout,chat,papers,screening,evidence,synthesis,draft,audit}/

src/features/

src/lib/

src/styles/

tests/

services/api/app/

api/v1/

core/ \# settings, auth, errors, logging

db/ \# session, models, migrations integration

schemas/ \# request/response and model-output schemas

repositories/ \# persistence access

services/ \# business logic

workflows/

graph.py

state.py

nodes/

routing.py

prompts/

agents/ \# role-specific orchestration wrappers

providers/{llm,scholarly,web_search,storage}/

documents/

screening/

extraction/

synthesis/

prisma/

audit/

exports/

tests/{unit,integration,workflow}/

services/worker/tasks/

docs/{requirements,architecture,implementation-reports,runbooks}/

infra/{docker,deployment}/

scripts/

.env.example

docker-compose.yml

pyproject.toml

README.md

API routes validate and delegate; services coordinate
repositories/providers; workflow nodes call services rather than
duplicating business rules.

# 15. Deployment and operations

- Provide Dockerfiles for the frontend, API and worker, plus
  docker-compose.yml for local development.

- Provide .env.example with variable names and explanations only; never
  real secrets.

- Document database migration, seed, test, build and startup commands.

- Provide liveness/readiness health endpoints. Readiness checks
  essential dependencies without expensive model calls.

- Separate development/staging/production configuration; never commit
  production data or PDFs.

- Configure request/job timeouts, retry limits, concurrency limits,
  upload limits, quotas and provider alerts.

- Provide deployment checklist: secrets, HTTPS, DB backup, storage
  permissions, CORS, migrations, logs and quotas.

- Prefer managed services when available to reduce operations burden;
  preserve portability through environment-based config and Docker.

# 16. Acceptance criteria for first complete release

- [ ] User can register/sign in and create a private project.

- [ ] Topic intake produces an editable protocol, framework suggestion,
  criteria and search plan.

- [ ] Primary search cannot start until the user approves the protocol.

- [ ] At least two scholarly source adapters work end-to-end with exact
  queries, timestamps, counts and errors saved.

- [ ] Records are normalized and deduplicated; uncertain matches are
  explainable.

- [ ] Paper cards show accessible relevance labels, abstract,
  source/access information, rationale and decision controls.

- [ ] User can opt into supplementary web discovery and upload
  institutionally accessed PDFs without paywall bypass.

- [ ] PDFs are validated, securely stored, parsed and matched; uncertain
  matches require confirmation.

- [ ] Title/abstract and full-text screening are distinct; full-text
  exclusions have reasons.

- [ ] Criteria and question changes are versioned and show downstream
  impact.

- [ ] PRISMA counts derive from stored records and pass consistency
  validation.

- [ ] User can select extraction fields or skip to defaults.

- [ ] Evidence extraction stores source locations and does not guess
  missing values.

- [ ] Evidence matrix is editable and supports comparison tables/export.

- [ ] Synthesis and review draft use verified papers and evidence-linked
  citations.

- [ ] User can pause, close browser, return later and resume without
  losing state.

- [ ] Audit timeline records meaningful actions and supports
  filtering/export.

- [ ] Dark IDE-style UI includes left chat, project explorer/navigation,
  main workspace and visible progress.

- [ ] Critical logic has unit tests; core integrations, human
  checkpoints, authorization and key user flows have automated tests.

- [ ] Documented setup works from a clean environment and deployment
  steps are documented.

- [ ] Every coding iteration produces a report with changes, rationale,
  tests, failures, limitations and next steps.

# 17. Build milestones

| **Milestone**          | **Deliverable**                                                                               | **Exit gate**                                             |
|------------------------|-----------------------------------------------------------------------------------------------|-----------------------------------------------------------|
| M0 Foundation          | Repo, lint/type checks, Docker, auth, DB/migrations, project CRUD                             | Fresh setup works; ownership tests pass.                  |
| M1 Chat + protocol     | Persistent chat, topic intake, protocol planner, framework suggestions, approval pause/resume | Versioning and resume tests pass.                         |
| M2 Search              | Two scholarly adapters, query logs, pagination, normalization, dedupe                         | Mocked integration tests and reproducibility checks pass. |
| M3 Discovery UI        | Paper cards, relevance labels/rationales, filters, supplementary search option                | Decisions persist across refresh.                         |
| M4 PDF + screening     | Secure upload, parsing, matching, title/abstract and full-text screening                      | Edge-case, authorization and loop tests pass.             |
| M5 PRISMA              | Protocol revisions, derived counts, flow diagram/checklist aid                                | Count invariants and edge cases pass.                     |
| M6 Extraction          | Field chips, schema versions, evidence provenance, editable matrix                            | Provenance tests and quality evaluation pass.             |
| M7 Synthesis + writing | Comparison tables, synthesis, editable draft, citations, DOCX export                          | Citation checks and export tests pass.                    |
| M8 Audit + hardening   | Full audit, quotas, load/resilience tests, deployment docs                                    | Acceptance checklist and security review pass.            |

Build vertical slices, not disconnected mock screens. A user action
should travel through UI → API → service/workflow → database/provider →
visible persisted result.

# 18. Risks and mitigations

| **Risk**                     | **Mitigation**                                                                                              |
|------------------------------|-------------------------------------------------------------------------------------------------------------|
| Relevant papers missed       | Multiple suitable sources, saved queries, coverage caveats, documented citation chasing, recall evaluation. |
| AI wrongly excludes a paper  | Uncertain state, conservative thresholds, human review, overrides and labeled evaluation.                   |
| Misleading relevance color   | Rationale, text labels, calibrated evaluation, no automatic exclusion by score.                             |
| Hallucinated extraction      | Schema validation, source quote/page, explicit missing states and human verification.                       |
| Fake/mismatched citation     | Resolve from stored records, claim-evidence links, fail validation if target is missing.                    |
| PRISMA totals conflict       | Derive counts from transitions and validate before generating diagrams.                                     |
| Free quota exhausted         | Queue/rate-limit/cache, show quota errors, save progress and resume.                                        |
| Provider changes             | Adapter interface, configurable model IDs, integration tests and documented fallback.                       |
| PDF prompt injection         | Treat documents as untrusted data and restrict tool permissions.                                            |
| Long jobs appear frozen      | Background workers, progress events, checkpointed state.                                                    |
| Conflicting concurrent edits | Optimistic concurrency/versioning.                                                                          |
| Scope grows too large        | Milestones; defer meta-analysis, microservices and advanced optional features.                              |

# 19. Assumptions to confirm during implementation

- The product owner will select an initial target discipline; universal
  extraction/appraisal support must not be claimed before evaluation.

- Gemini free tier is the initial development option. Current model
  availability, quotas, pricing, data terms and production suitability
  must be checked at implementation time.

- Use official/supported scholarly APIs and a supported search provider.
  Automated Google Scholar scraping is not a default dependency.

- The user supplies PDFs for content that is not openly accessible. No
  paywall circumvention.

- Human approvals are required at protocol approval, key screening
  decisions, extraction review, synthesis review and final draft review.

- The first release produces a literature-review draft and reporting
  artifacts, not a guarantee of a publication-ready systematic review.

- Hosting provider is selected after checking budget, region, storage
  and provider quotas.

# 20. Reference links

- **Official PRISMA 2020 statement/checklist/flow diagrams:**
  https://www.prisma-statement.org/prisma-2020

- **PRISMA 2020 checklist:**
  https://www.prisma-statement.org/prisma-2020-checklist

- **User-provided secondary PRISMA guide:**
  https://adpublications.org/blog/how-to-write-a-systematic-literature-review-prisma-guide

- **Gemini API pricing:** https://ai.google.dev/gemini-api/docs/pricing

- **Gemini API billing:** https://ai.google.dev/gemini-api/docs/billing

- **OpenAlex API docs:** https://docs.openalex.org/

- **Crossref REST API docs:**
  https://www.crossref.org/documentation/retrieve-metadata/rest-api/

- **Europe PMC REST API:** https://europepmc.org/RestfulWebService

- **NCBI E-utilities:** https://www.ncbi.nlm.nih.gov/books/NBK25501/

- **arXiv API:** https://info.arxiv.org/help/api/index.html

- **LangGraph documentation:**
  https://docs.langchain.com/oss/python/langgraph/overview

- **FastAPI documentation:** https://fastapi.tiangolo.com/

- **Next.js documentation:** https://nextjs.org/docs

- **Pydantic documentation:** https://docs.pydantic.dev/

# Final instruction to the implementation AI

Before coding, inspect the repository and produce a short plan mapped to
the milestones. Implement one vertical slice at a time. Preserve module
boundaries, typed schemas, tests, and clear documentation. After each
iteration, report exactly what changed, why it changed, which
alternatives were considered, which tests actually ran, what failed, and
what remains. Never fabricate research records, citations, test results
or deployment status. When requirements conflict, prioritize scientific
traceability, user control, privacy/security and data integrity over
apparent autonomy.
