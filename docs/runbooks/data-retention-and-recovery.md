# Data retention, deletion, backup and restore (Phase 14)

## What is stored and for how long

| Data | Where | Retention | Deleted by |
|---|---|---|---|
| Account (email, Argon2id hash) | `users` | while the account exists | operator procedure below |
| Sessions (token **hash**, expiry, user agent) | `auth_sessions` | until `SESSION_RETENTION_DAYS` (30) after expiry or revocation | scheduled sweep (`system.purge_documents` beat task) |
| Projects and all research data (protocol versions, records, decisions, evidence, appraisals, syntheses, drafts, pipeline runs, chat) | project tables | while the project exists | project deletion (cascade) |
| Uploaded PDFs | object storage `projects/<project_id>/…` | removed files: `DOCUMENT_REMOVED_RETENTION_DAYS` (30), then file + page text purged; deleted project: immediately (orphan sweep catches failures after `DOCUMENT_ORPHAN_GRACE_S`) | `document_service.purge` |
| Extracted page text | `document_pages` | with the document | purge / project deletion |
| Audit events | `audit_events` | **indefinitely** (append-only; contain ids, action names and changed field names — no titles, topics, chat or document text; failed sign-ins store an email fingerprint, not the email) | never through the application |
| Workflow checkpoints (may contain protocol answers) | schema `lg_checkpoints` | while the project exists | project deletion deletes the threads of its current workflow/pipeline runs (best effort, logged); threads abandoned by an earlier protocol rebuild are not yet swept (known gap) |
| Logs | stdout (JSON, redacted) | operator's log platform policy (recommend ≤ 30 days) | log platform |
| Backups | operator storage | operator policy (recommend 30 daily + 12 monthly, encrypted, off-host) | operator |

Deleting a project removes its rows and files but **keeps its audit events** (they have no foreign keys by design),
so the history of what happened survives without the research content.

### Account deletion (operator procedure; no self-service yet)
1. Ask the user to delete their projects in the UI (or, as operator, delete them through the API on their behalf).
2. `docker compose exec postgres psql -U srs -d srs -c "UPDATE users SET status='disabled' WHERE email='…'"` — sessions stop
   resolving immediately; then delete the row once no `jobs` reference it (`jobs.owner_id` is `RESTRICT`).
3. Audit events about the user remain (ids only). Record the request outside the system if required.

## Backups
`scripts/backup.sh [OUT_DIR]` writes `db.dump` (pg_dump custom format), `storage.tar.gz` (the upload volume) and a
`manifest.txt` with checksums, the git commit and the Alembic head. Run it from cron at least daily (production: `COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml" scripts/backup.sh /srv/backups`, see `deployment.md`); copy the
directory off-host, encrypted. The database is dumped first: a file stored between the two steps has no row and is
removed by the orphan sweep after a restore; a row whose file is missing shows an error in the Files tab rather than
pretending the document is available.

Redis holds only queue messages, rate-limit counters and transient job state; it is not backed up. After a restore the
job sweeper re-queues or fails stale jobs (`job_service.sweep`).

## Restore
`scripts/restore.sh BACKUP_DIR --yes` verifies checksums, stops the app services, restores the database
(`pg_restore --clean`), replaces the storage volume, runs `alembic upgrade head` and starts everything.

Verification checklist after a restore:
1. `curl -fsS http://localhost:8000/readyz` returns ok for database and broker; `/healthz/worker` answers.
2. Sign in as a known user; the project list and a project's **Audit log** tab show the expected latest events.
3. Open a processed document's text view; download the original file.
4. The PRISMA Flow tab shows no new inconsistencies.
5. `docker compose exec postgres psql -U srs -d srs -c "TRUNCATE audit_events"` is refused (append-only trigger restored).

Run a restore drill into a scratch deployment at least quarterly and record the date and outcome.
