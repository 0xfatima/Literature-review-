# External provider data handling and consent gate (ADR amendment A4)

**Policy: transmission of private documents to an external LLM provider is DISABLED by default and stays disabled until ALL of the following are true.** This is enforced in a backend gateway, not in the UI.

| Gate | Where enforced | Default |
|---|---|---|
| G1 Operator flag `LLM_SEND_PRIVATE_DOCUMENTS=true` | `LLMGateway` (Phase 2) | `false` |
| G2 Provider terms record below is marked **Reviewed**, with reviewer, date, tier and URL | gateway reads the record; refuses if status ≠ reviewed or older than the review interval | **Not reviewed** |
| G3 The project owner has an active, un-revoked `llm_consents` row for that provider/model/data class, created after seeing a plain-language notice | gateway checks DB | none |
| G4 Every `model_runs` row records the `data_class` sent (`public_metadata` or `private_document`) | service layer | — |

Revoking consent stops further transmission immediately; it cannot recall data already sent (the notice must say so).

## Data classes
- `public_metadata`: titles, abstracts, bibliographic fields obtained from public scholarly APIs. Still disclosed to the user as being sent to the provider.
- `private_document`: user-uploaded PDFs and text derived from them; also chat content that contains them. **Gated as above.**

## Provider terms record (to be completed by a human from the provider's CURRENT terms)

| Field | Value |
|---|---|
| Provider / product / tier | _Gemini API — tier(s) to be listed_ |
| Terms & data-use URL(s) | _not yet recorded_ |
| Is submitted content used to improve provider products on this tier? | **UNVERIFIED.** Consumer/free tiers of some providers differ from paid tiers here; confirm in writing from the current terms. |
| Retention period / abuse-monitoring retention | _unverified_ |
| Region / processing location | _unverified_ |
| Ability to opt out / zero-data-retention arrangement | _unverified_ |
| Reviewed by / date | _blank — status: NOT REVIEWED_ |

No claim about any provider's data practices has been made by this project; this table is a checklist, not evidence.

## Implementation notes (Phase 2)
- `llm_consents`: `project_id, user_id, provider, model_id, data_class, terms_record_version, consented_at, revoked_at`. Consent is per project, per provider/model, per data class.
- The notice shown at consent time is versioned and stored by version.
- `FakeLLM` (tests/offline) never needs consent. Live evals use public metadata only.
- Phase 1 adds **no** LLM code, so no data leaves the system in this phase.

## Phase 5 status
- Implemented: `LLMGateway` with the data-class gate, `model_runs` (records `data_class` for every call: gate G4).
- Phase 5 sends `public_metadata` only (relevance assessment of titles/abstracts + the approved protocol).
- `private_document` is refused; G1-G3 (operator flag, reviewed terms record, per-project consent) remain to be built
  before any document text is sent (extraction phases). The provider terms record above is still NOT REVIEWED.
- Free-tier caveat shown in the UI: content sent on Google's free tier may be used to improve Google's products.

## Phase 9 status (evidence extraction)
- **All four gates are now enforced before any `private_document` call**, inside `LLMGateway.generate_json`:
  - G1 `LLM_SEND_PRIVATE_DOCUMENTS=true` (startup outside development/test refuses it without a terms record).
  - G2 `LLM_TERMS_RECORD_VERSION` + `LLM_TERMS_REVIEWED_ON` set by the operator after reviewing the provider's current
    terms; expires after `LLM_TERMS_REVIEW_INTERVAL_DAYS` (default 365) and is refused if dated in the future.
    This repository still makes **no claim** about any provider's terms; the table above remains a checklist that a
    human must complete and keep with the deployment records.
  - G3 an active `llm_consents` row for the project and the exact provider/model, created by the owner in the
    Tables tab after reading the versioned notice (`document-extraction-notice-v1`). Revoking takes effect for the
    next request; the notice says text already sent cannot be recalled.
  - G4 every extraction request writes a `model_runs` row with `data_class='private_document'`, on failure too.
- What is sent: the approved field definitions, the paper title, and the page text of the linked, parsed PDF of an
  included paper, in page-aligned chunks (`EXTRACTION_CHUNK_CHARS`). Nothing else from the project is sent.
- `FakeLLM`-style test providers are constructed with the gates set explicitly; tests never contact a provider.
