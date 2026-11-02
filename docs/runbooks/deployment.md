# Production deployment (Phase 15)

One host running Docker Compose v2.24+ is the documented target (`docker-compose.prod.yml`). The same images and
settings work on any container platform; the rules in "Topology" are what must be preserved.

## Topology

| Service | Image | Exposed | Notes |
|---|---|---|---|
| `proxy` | nginx 1.27 | **80, 443** | TLS termination, HTTP→HTTPS, HSTS, ACME webroot. The only public service. Never forwards `/internal/`. |
| `web` | `srs-web` | internal | Next.js; reaches the API on the internal network. |
| `api` | `srs-api` | internal | One Uvicorn process per container; scale with replicas. `TRUSTED_PROXY_HOPS=1` (the proxy). |
| `worker-interactive` | `srs-worker` | internal | Queues `interactive,system,celery`: protocol steps, searches, relevance, synthesis, pipeline, sweeps. |
| `worker-heavy` | `srs-worker` | internal | Queue `heavy`: PDF parsing/OCR, AI extraction and appraisal. |
| `beat` | `srs-worker` | internal | **Exactly one.** Schedules the sweeper (60 s), the admission pump (5 s) and the purge. |
| `migrate` | `srs-api` | — | One-shot `alembic upgrade head` before app services start. |
| `postgres` | postgres 16 | internal | Source of truth. Volume `pgdata`. |
| `redis` | redis 7 | internal | Password-protected, AOF. Broker, rate limits, throttles, AI semaphore. Losing it loses no state (jobs live in Postgres). |

Volumes: `pgdata`, `redisdata`, `storage` (uploaded PDFs, shared by `api` and workers), `acme`.

## Secrets
`scripts/gen-secrets.sh` creates `./secrets/` (directory mode 700 so no other host user can reach it; files 644
because Compose bind-mounts file secrets with host permissions and the containers run as a non-root user; git-ignored): `postgres_password`, `redis_password`,
`database_url`, `redis_url`, `session_secret`, `metrics_token`, `llm_api_key` (empty = AI disabled). They are mounted
as Docker secrets and read through `SECRETS_DIR=/run/secrets` (one file per setting); they never appear in
environment variables, compose files, logs or images. Store copies in your password manager. Rotating
`session_secret` signs everyone out; rotating database/Redis passwords needs both the `*_password` and the `*_url` file
changed and `scripts/deploy.sh` re-run.

## Configuration
`cp .env.production.example .env.production` and set at least `APP_BASE_URL`, `API_BASE_URL`,
`CORS_ALLOWED_ORIGINS` (all `https://your-host`), `SCHOLARLY_CONTACT_EMAIL` and `SRS_VERSION`. Startup refuses unsafe
production settings (non-https endpoint overrides, disabled write limit, missing secure cookies, weak session secret).
Capacity settings and the measured basis for them are in `docs/performance.md`.

## TLS
Place `fullchain.pem` and `privkey.pem` in `infra/proxy/certs/` (git-ignored) or point `TLS_CERT_DIR` at them.
With Let's Encrypt (HTTP-01), after the first deploy:

```bash
docker run --rm -v srs-prod_acme:/var/www/acme -v /etc/letsencrypt:/etc/letsencrypt certbot/certbot \
  certonly --webroot -w /var/www/acme -d reviews.example.org --agree-tos -m ops@example.org -n
# then TLS_CERT_DIR=/etc/letsencrypt/live/reviews.example.org in .env.production and re-run scripts/deploy.sh
```
Renew with the same container (`certbot renew`) from cron, then `docker compose ... exec proxy nginx -s reload`.
For a first boot before a real certificate exists, a self-signed pair (as CI does) lets the stack start:
`openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj /CN=localhost -keyout infra/proxy/certs/privkey.pem -out infra/proxy/certs/fullchain.pem`.

## Deploy / upgrade
```bash
git fetch && git checkout <release>        # set SRS_VERSION in .env.production to the same value
scripts/deploy.sh                           # add --insecure only for a self-signed staging certificate
```
`deploy.sh` runs pre-flight checks (config, secret files and permissions, certificate, https base URL), builds the
images, **takes a backup before upgrading an existing installation**, starts Postgres/Redis, applies migrations,
(re)starts the app services, waits for `/readyz`, then runs `scripts/smoke.py` against the public URL. It is
idempotent: run it for the first install and for every upgrade.

**Migrations** are forward-only and additive in this codebase (new columns are nullable or defaulted; indexes are
created before the code that uses them), so an older API process keeps working during the short restart window.
Workers also consume the legacy `celery` queue, so jobs sent by a not-yet-restarted API are never stranded.

**Rollback**: `git checkout <previous release>` and re-run `scripts/deploy.sh`. A migration that must be reverted is
reverted with the backup taken by the failed deploy (`scripts/restore.sh backups/srs-<timestamp> --yes`), not with
`alembic downgrade` on production data.

## Stopping and restarting safely
`docker compose ... stop` sends SIGTERM. The API finishes in-flight requests (≤25 s). Workers stop taking new jobs and
running jobs stop at their next checkpoint (search page, AI batch, appraisal item) with their progress saved; the
90 s stop grace period exceeds `WORKER_DRAIN_TIMEOUT_S`. Anything killed anyway is redelivered (acks-late) or requeued
by the sweeper; duplicate deliveries are ignored by the job claim. No step needs manual cleanup after a restart.

## Smoke test
`python3 scripts/smoke.py --base https://reviews.example.org` (stdlib only; any machine). It checks proxy→API and
proxy→web, HSTS, 401 JSON for anonymous API calls, `no-store` caching, that `/internal/metrics` is not public, then
signs up a throwaway user (`smoke-…@example.invalid`), creates and deletes a project, runs a background job through
the broker/worker/admission pump, checks the audit log and owner isolation. Smoke users remain (no self-service
deletion yet); they own no data after the run.

## Backups and the restore exercise
Schedule `scripts/backup.sh` (see `data-retention-and-recovery.md`) with
`COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml"`. Prove restores work on staging:

```bash
COMPOSE="docker compose --env-file .env.production -f docker-compose.prod.yml" scripts/restore-drill.sh https://staging.example.org
```
The drill creates a marker user + project + uploaded PDF (parsed by the worker), takes a backup, deletes the project,
restores the backup, and verifies the project, its audit history and the PDF file itself are back. CI runs the same
drill on every pull request against the production compose file with a self-signed certificate (`deploy-verify`).

## Monitoring
`GET /internal/metrics` (Prometheus text) on the internal network with `Authorization: Bearer <metrics_token>`, e.g.
`docker compose ... exec api curl -s -H "Authorization: Bearer $(cat /run/secrets/metrics_token)" localhost:8000/internal/metrics`.
Suggested alerts: `srs_jobs_oldest_queued_age_seconds > 600`; `srs_job_failure_ratio > 0.2`; p95 of
`srs_http_request_duration_seconds` above the targets in `docs/performance.md`; `srs_llm_tokens_24h` above 80 % of
`LLM_GLOBAL_DAILY_TOKEN_BUDGET`; `srs_scholarly_rate_limited_queries > 0` sustained; `/readyz` failing.
