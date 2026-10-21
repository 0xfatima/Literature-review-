# Database entity relationship diagram

Spec §8 tables plus additions marked ★ (rationale in ADR-0001 §4). Only key columns are shown. All PKs are UUIDs. Every project-scoped table carries `project_id` (directly or via parent) so ownership can be enforced server-side.

> **Implemented by migration `0001_baseline` (Phase 1):** `users`, `auth_sessions`, `projects`, `audit_events`, `jobs`.
> Every other table is design intent for a later phase. `password_reset_tokens` was **removed** (password recovery deferred, ADR amendment A8).
> `llm_consents` (A4) and `studies` + PRISMA fields (A6) are **new** since Phase 0.
>
> Mermaid source below was **not rendered** in Phase 0 (no renderer available offline); structural consistency was checked by script (see report).

```mermaid
erDiagram
  users ||--o{ projects : owns
  users ||--o{ auth_sessions : has
  projects ||--o{ conversations : has
  conversations ||--o{ messages : contains
  projects ||--o{ protocol_versions : versions
  protocol_versions ||--o{ protocol_questions : defines
  protocol_versions ||--o{ protocol_criteria : defines
  projects ||--o{ search_runs : runs
  protocol_versions ||--o{ search_runs : approved_for
  projects ||--o{ paper_records : collects
  paper_records ||--o{ paper_source_records : seen_in
  search_runs ||--o{ paper_source_records : returned
  paper_records ||--o{ dedupe_candidates : candidate_a
  paper_records ||--o{ paper_documents : has_pdf
  paper_documents ||--o{ document_pages : pages
  paper_records ||--o{ screening_decisions : screened
  protocol_versions ||--o{ screening_decisions : under
  screening_decisions ||--o{ screening_decision_criteria : assesses
  protocol_criteria ||--o{ screening_decision_criteria : cited_by
  model_runs ||--o{ screening_decisions : proposed_by
  paper_records ||--o{ record_status_events : transitions
  protocol_versions ||--o{ record_status_events : under
  projects ||--o{ extraction_schemas : versions
  extraction_schemas ||--o{ evidence_items : shapes
  paper_records ||--o{ evidence_items : about
  document_pages ||--o{ evidence_items : sourced_from
  evidence_items ||--o{ evidence_item_revisions : history
  model_runs ||--o{ evidence_items : proposed_by
  paper_records ||--o{ appraisal_records : appraised
  projects ||--o{ synthesis_versions : versions
  projects ||--o{ draft_versions : versions
  synthesis_versions ||--o{ draft_versions : feeds
  draft_versions ||--o{ claim_evidence_links : cites
  paper_records ||--o{ claim_evidence_links : target
  evidence_items ||--o{ claim_evidence_links : supports
  protocol_questions ||--o{ question_coverage : covered
  evidence_items ||--o{ question_coverage : supports
  projects ||--o{ workflow_runs : runs
  projects ||--o{ llm_consents : grants
  users ||--o{ llm_consents : gives
  projects ||--o{ studies : groups
  studies ||--o{ paper_records : reports
  workflow_runs ||--o{ model_runs : calls
  users ||--o{ jobs : owns
  projects ||--o{ jobs : queues
  jobs ||--o{ jobs : rerun_of
  workflow_runs ||--o{ jobs : executes

  users {
    uuid id PK
    string email UK
    string password_hash
    string status
    timestamptz created_at
  }
  auth_sessions {
    uuid id PK
    uuid user_id FK
    string token_hash UK
    timestamptz expires_at
    timestamptz revoked_at
  }
  projects {
    uuid id PK
    uuid owner_id FK
    string title
    text topic_original
    string discipline
    string review_type
    string status
    int row_version
    timestamptz archived_at
  }
  conversations {
    uuid id PK
    uuid project_id FK
    string title
  }
  messages {
    uuid id PK
    uuid conversation_id FK
    string role
    text content
    jsonb structured_payload
    timestamptz created_at
  }
  protocol_versions {
    uuid id PK
    uuid project_id FK
    int version UK
    string framework
    jsonb filters_and_sources
    uuid extraction_schema_id
    string status
    uuid approved_by
    timestamptz approved_at
    text change_reason
    uuid supersedes_id
  }
  protocol_questions {
    uuid id PK
    uuid protocol_version_id FK
    uuid stable_key
    text text
    int position
  }
  protocol_criteria {
    uuid id PK
    uuid protocol_version_id FK
    uuid stable_key
    string kind
    text text
    text rationale
    string state
  }
  search_runs {
    uuid id PK
    uuid project_id FK
    uuid protocol_version_id FK
    string provider
    text exact_query
    jsonb filters
    jsonb cursor
    string status
    int result_count
    jsonb errors
    timestamptz started_at
    timestamptz finished_at
  }
  paper_records {
    uuid id PK
    uuid project_id FK
    string doi
    text title
    text abstract
    jsonb authors
    int year
    string venue
    string access_status
    string normalized_hash
    string work_type
    uuid merged_into_id
    uuid study_id FK
  }
  paper_source_records {
    uuid id PK
    uuid paper_id FK
    uuid search_run_id FK
    string provider
    string provider_record_id
    string source_url
    jsonb raw_metadata
  }
  dedupe_candidates {
    uuid id PK
    uuid paper_a_id FK
    uuid paper_b_id FK
    float similarity
    jsonb reasons
    string resolution
    uuid resolved_by
  }
  paper_documents {
    uuid id PK
    uuid paper_id FK
    uuid uploader_id FK
    string storage_key
    string sha256
    bigint size_bytes
    int page_count
    string parse_status
    int text_version
    string match_status
  }
  document_pages {
    uuid id PK
    uuid document_id FK
    int page_no
    text text
    int text_version
    bool ocr_used
  }
  screening_decisions {
    uuid id PK
    uuid paper_id FK
    uuid protocol_version_id FK
    string stage
    string decision
    string exclusion_reason
    text rationale
    text excerpt
    uuid model_run_id FK
    uuid reviewer_id
    uuid supersedes_id
    bool is_stale
    timestamptz decided_at
  }
  screening_decision_criteria {
    uuid id PK
    uuid screening_decision_id FK
    uuid protocol_criterion_id FK
    string assessment
    text excerpt
  }
  record_status_events {
    uuid id PK
    uuid paper_id FK
    uuid protocol_version_id FK
    string pathway
    string from_status
    string to_status
    string source_tag
    string reason_code
    uuid actor_id
    timestamptz at
  }
  extraction_schemas {
    uuid id PK
    uuid project_id FK
    int version
    jsonb field_definitions
    timestamptz created_at
  }
  evidence_items {
    uuid id PK
    uuid paper_id FK
    uuid extraction_schema_id FK
    string field_key
    jsonb value
    string status
    uuid document_page_id FK
    string section
    text excerpt
    uuid model_run_id FK
    uuid reviewer_id
    string review_state
    int row_version
  }
  evidence_item_revisions {
    uuid id PK
    uuid evidence_item_id FK
    jsonb before
    jsonb after
    uuid actor_id
    text reason
    timestamptz at
  }
  appraisal_records {
    uuid id PK
    uuid paper_id FK
    string framework
    jsonb judgments
    string status
    uuid reviewer_id
  }
  synthesis_versions {
    uuid id PK
    uuid project_id FK
    int version
    jsonb themes_and_claims
    string status
  }
  draft_versions {
    uuid id PK
    uuid project_id FK
    uuid synthesis_version_id FK
    int version
    jsonb sections
    string status
  }
  claim_evidence_links {
    uuid id PK
    uuid draft_version_id FK
    string claim_id
    uuid paper_id FK
    uuid evidence_item_id FK
    string citation_locator
  }
  question_coverage {
    uuid id PK
    uuid protocol_question_id FK
    uuid evidence_item_id FK
    string indicator
    text gap_note
  }
  llm_consents {
    uuid id PK
    uuid project_id FK
    uuid user_id FK
    string provider
    string model_id
    string data_class
    string terms_record_version
    timestamptz consented_at
    timestamptz revoked_at
  }
  studies {
    uuid id PK
    uuid project_id FK
    string label
  }
  workflow_runs {
    uuid id PK
    uuid project_id FK
    string graph_version
    string stage
    string status
    jsonb pending_question
    int iteration_count
    uuid correlation_id
  }
  model_runs {
    uuid id PK
    uuid workflow_run_id FK
    string provider
    string model_id
    string prompt_version
    jsonb settings
    int tokens_in
    int tokens_out
    int duration_ms
    string status
  }
  jobs {
    uuid id PK
    uuid owner_id FK
    uuid project_id FK
    uuid workflow_run_id FK
    uuid rerun_of_job_id FK
    string type
    string status
    int progress
    string request_key
    string input_fingerprint
    jsonb input
    int attempts
    int max_attempts
    bool cancel_requested
    text error_summary
    timestamptz heartbeat_at
  }
  audit_events {
    uuid id PK
    uuid project_id
    uuid actor_id
    string action
    string entity_type
    uuid entity_id
    jsonb before
    jsonb after
    text reason
    uuid correlation_id
    timestamptz at
  }
```

## Key constraints and indexes (to be implemented with each phase's migration)

| Table | Constraint / index | Why |
|---|---|---|
| `users` | unique `lower(email)` | account identity |
| `protocol_versions` | unique `(project_id, version)`; approved rows immutable (trigger or service guard + test) | spec §3.2, rule 16 |
| `search_runs` | index `(project_id, provider, started_at)` | reproducibility listing |
| `paper_records` | index `(project_id, doi)`; partial unique `(project_id, lower(doi)) WHERE doi IS NOT NULL AND merged_into_id IS NULL`; index `(project_id, normalized_hash)` | dedupe, filters |
| `paper_source_records` | unique `(search_run_id, provider, provider_record_id)` | idempotent retry |
| `paper_documents` | unique `(paper_id, sha256)` | no duplicate uploads |
| `document_pages` | unique `(document_id, page_no, text_version)` | provenance |
| `screening_decisions` | index `(paper_id, stage, protocol_version_id)`; CHECK `stage='full_text' AND decision='exclude' ⇒ exclusion_reason IS NOT NULL` | spec §3.6 |
| `evidence_items` | unique `(paper_id, extraction_schema_id, field_key)`; CHECK `status='extracted' ⇒ excerpt IS NOT NULL AND document_page_id IS NOT NULL` (with explicit exceptions enumerated in code) | provenance (rule 9) |
| `screening_decision_criteria` | unique `(screening_decision_id, protocol_criterion_id)`; `assessment` in `met\|not_met\|unclear` | criterion-level rationale (spec §3.6) |
| `jobs` | partial unique `(owner_id, request_key) WHERE request_key IS NOT NULL`; partial unique `(owner_id, type, COALESCE(project_id, zero-uuid), input_fingerprint) WHERE status IN ('queued','running')` | replay-safe submission **and** no duplicate in-flight work, **without** blocking legitimate reruns (A5) |
| `audit_events` | indexes `(project_id, at)`, `(actor_id, at)`; **no foreign keys**; BEFORE UPDATE/DELETE row trigger and BEFORE TRUNCATE statement trigger raise | append-only for every DB role; events outlive deleted projects |
| editable rows | `row_version` optimistic concurrency (`projects`, `protocol_versions` draft, `evidence_items`, `draft_versions`, `screening_decisions` via supersession) | spec §8 |

## Notes
- `paper_records.merged_into_id`: merges are reversible pointers, never deletes, so PRISMA "duplicates removed" counts remain derivable.
- `screening_decisions` are append-only; an override is a new row with `supersedes_id`.
- Large text (`document_pages.text`) is isolated from hot tables; raw PDFs live in object storage, referenced by `storage_key`.
- **Deletion/retention (decided in Phase 1):** deleting a project hard-deletes the project and all dependent rows (cascade) and, in later phases, purges its stored objects. `audit_events` are retained (ids, action names, timestamps, actor) and contain **no free-text user content** (titles/topics are never written to the log; only changed field *names*). Account deletion is not implemented yet (`users` FKs are RESTRICT) and is listed as a limitation.
