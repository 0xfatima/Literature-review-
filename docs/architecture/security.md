# Security and auditability (Phase 14)

Scope: a single-tenant-per-project modular monolith (Next.js → nginx → FastAPI → PostgreSQL/Redis/Celery). This page
lists the controls, where they live and how they are tested. Data handling for AI providers is in
`provider-data-handling.md`; retention, deletion, backup and restore are in `../runbooks/data-retention-and-recovery.md`.

## Threats considered and controls

| Threat | Control | Code | Tests |
|---|---|---|---|
| Reading or changing another user's project | Every `/projects/{id}/…` route depends on `get_owned_project` (404, never 403, so existence is not leaked); files, jobs, messages and workflow routes go through it | `app/api/deps.py` | `test_authorization_matrix.py`, drill `ownership`, `test_security_hardening.py::test_audit_log_is_private_and_read_only` |
| Session theft / fixation | Opaque 256-bit tokens, only their SHA-256 stored; HttpOnly, SameSite=Lax, `Secure` required outside dev/test; logout and operator reset revoke; ended rows deleted after `SESSION_RETENTION_DAYS` | `core/security.py`, `services/auth_service.py` | `test_auth_flow.py`, `test_security_hardening.py::test_ended_sessions_are_purged_after_retention` |
| CSRF | Custom header `X-SRS-CSRF` + Origin/Referer allow-list on every state-changing `/api` request; CORS exact allow-list | `core/security.py`, `main.py` | `test_security_primitives.py`, drill `csrf` |
| Credential stuffing / brute force | Per-IP and per-account login limits, registration limit (Redis, shared across replicas) | `services/auth_service.py` | `test_rate_limiter.py`, drill `ip-limit` |
| Resource exhaustion | Per-session limit on state-changing requests (`RATE_LIMIT_WRITES_PER_MIN`); request-body ceiling (`MAX_REQUEST_BODY_BYTES`, uploads `UPLOAD_MAX_BYTES`) at nginx **and** in the API; per-feature AI/search/upload limits; token budgets and concurrency caps (Phase 13); upload quotas; parse in a resource-limited child process | `core/http_security.py`, `main.py`, `infra/proxy/nginx.conf`, `documents/runner.py` | `test_http_security.py`, `test_security_hardening.py` |
| Malicious uploads | Magic-byte/structure validation, size and page limits, text-bomb guard, generated storage keys, private storage, optional ClamAV, attachment-only download with `sandbox` CSP | `documents/validation.py`, `providers/malware`, `api/v1/documents.py` | `test_document_validation.py`, `test_documents.py` |
| Browser-side attacks on API responses | `nosniff`, `X-Frame-Options: DENY`, `CSP default-src 'none'; frame-ancestors 'none'`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, HSTS when `COOKIE_SECURE=true` | `core/http_security.py` | `test_http_security.py`, `test_security_hardening.py::test_security_headers_on_api_responses` |
| Prompt injection via papers / uploads | All third-party text is fenced and neutralised (`fence()`), forged page markers cannot alter provenance, the gateway appends the untrusted-content policy to every system prompt, model output is schema-validated JSON checked against the source text and **never** executed or turned into tool calls; injection heuristics are logged as signal names only | `core/untrusted.py`, `providers/llm/base.py`, prompt builders | `test_untrusted_content.py` |
| Secrets in logs / audit | Key- and pattern-based redaction in the JSON log formatter and in audit payloads; audit events store field names, not free text; no request bodies or query strings in logs | `core/logging.py`, `services/audit_service.py` | `test_logging_redaction.py`, `test_audit.py` |
| Provider credentials exposed | API keys only in server env (`.env`, never in the web bundle); Next.js talks to the API same-origin | `core/config.py` | `test_config_env_file.py` |
| Vulnerable dependencies | Exact lockfiles; `dependency-audit` CI job (pinned `pip-audit` on `requirements.lock.txt`, `npm audit --omit=dev --audit-level=high`) is required by `ci-gate`; Dependabot weekly PRs | `.github/workflows/ci.yml`, `.github/dependabot.yml` | CI |

## Audit trail

* **What is recorded.** Searches, uploads/downloads/removals/purges, protocol drafts/revisions/approvals (criteria
  changes), screening and full-text decisions (including undo/corrections as new events), extraction schema and value
  edits/approvals, appraisals, synthesis acceptance, report/evidence/audit exports, AI consent, pipeline actions,
  sign-ins (successful and failed), sign-outs and operator resets. `app/audit/catalog.py::REQUIRED` maps each
  significant event class to action names; `test_audit_catalog.py` fails if any is no longer emitted.
* **Who.** `actor_id` for a person; for system work `actor_id` is null and `actor_process` names the process
  (`worker:<celery task>`, `cli:<command>`), set by Celery task signals and the CLI. Every event also carries the
  request/job correlation id and a database timestamp.
* **Append-only.** A trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` on `audit_events` (drill
  `audit-immutable`). The API exposes read and CSV export only; there is no edit or delete route (405). Corrections
  are new events written by the feature that made them.
* **Reading.** `GET /api/v1/projects/{id}/audit-events` (owner only; filters `category`, `entity_type`, `actor`;
  keyset cursor), `GET …/audit-events/export.csv` (formula-neutralised CSV, the export itself is audited) and
  `GET /api/v1/auth/me/audit-events` (account events). The workspace has an **Audit log** tab.
* **Model provenance.** Every AI call stores provider, model, prompt version, settings, tokens and status in
  `model_runs` (Phases 5/9–11); the untrusted-content policy version is `untrusted-v1` (logged with injection signals).

## Known limitations

* The web CSP does not restrict `script-src` (Next.js inline bootstrap needs per-request nonces via middleware);
  framing, plugins, `base` and form targets are restricted.
* The per-session write limiter fails open if Redis is unavailable (availability over lockout; logged), like the
  login limiter. The body ceiling relies on `Content-Length`; chunked bodies are bounded by nginx.
* The audit CSV export is a GET that also writes an audit row; it is idempotent in effect and harmless if triggered
  cross-site (SameSite cookie, owner-only, no data leaves the origin), but it is not CSRF-checked.
* Per-feature `PROMPT_VERSION` strings were not bumped for the fencing change because some key cached results (bumping
  `relevance-v1` would hide existing assessments); the change is identified by `untrusted-v1` instead.
* Heuristic injection detection is monitoring only; the defence is fencing + no tool execution + validation.
* Malware scanning is optional (`MALWARE_SCANNER=clamav`); without it uploads are only structurally validated.
* No self-service account deletion yet (see the runbook for the operator procedure).
