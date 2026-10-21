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
