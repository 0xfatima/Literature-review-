# Production implications review (Phase 1 validation, item 7)

Method: code reading and design analysis only. **None of this was exercised against a running system** (no runtime available). New defects found by this review are marked **FIXED-UNVERIFIED** (code changed, executable tests added/run where possible) or **OPEN**.

Two launch tiers are used, because the right answer differs:
- **Pilot** = invite-only, known users who have been told about the limitations, operator on call.
- **Public** = open sign-up, unknown users, real research data, legal exposure.

Classification: **BLOCKER** (must be fixed before that tier) · **ACCEPTED** (explicitly accepted with stated conditions) · **n/a**.

## 1. Fail-open rate limiting (`RedisRateLimiter`)
**Behaviour:** if Redis errors, `hit()` returns "allowed" and logs a warning, so login and registration are unthrottled while Redis is down.
**Risk:** brute-force/credential-stuffing and registration spam during any Redis outage (or an attacker who can degrade Redis). Argon2id cost (tens–hundreds of ms of CPU per guess) bounds guess *rate per CPU* but is not a control; it also makes unthrottled login a CPU-exhaustion vector. Note Redis down also stops job processing, so operators will likely notice quickly.
**Related functional finding:** the limiter counts *successful* logins in the per-IP bucket (10/min). Many users behind one NAT (an office, a university) can lock each other out. Per-account bucket is fine.
| Tier | Verdict | Condition / required action |
|---|---|---|
| Pilot | **ACCEPTED** | Alert on Redis unavailability (`/readyz` redis=error); pilot users are known; per-account bucket tuned. |
| Public | **BLOCKER** | Replace fail-open with a degraded-mode limiter: per-process in-memory fallback with *stricter* limits (or fail-closed for `/auth/login` returning 503). Count only failed attempts in the per-IP bucket; size buckets for NAT. Add tests: Redis down ⇒ limiter still throttles. |

## 2. Trusted proxy headers / client-IP derivation
**Defects found by this review (both real):**
1. **Global bucket (OPEN→FIXED-UNVERIFIED):** behind the Next.js proxy every request arrives from the web container, so per-IP limits keyed on the TCP peer were *one global bucket*: a single attacker sending 11 bad logins/min would lock out **every** user (availability DoS). Phase-1 code had this flaw.
2. **Spoofable header (FIXED-UNVERIFIED):** the Phase-1 `trust_forwarded_headers` option took the **first** `X-Forwarded-For` entry; proxies *append*, so the first entry is attacker-controlled and any rate-limit bucket could be evaded.
**Fix:** `TRUSTED_PROXY_HOPS=N` and `app/core/net.client_ip`: use the N-th entry from the **right**; ignore everything left of it; fall back to the TCP peer if the chain is shorter than N or the value is not an IP. 7 unit tests **executed (pass)**, including a mutation that restores first-entry semantics (5 tests fail).
**Still unverified (needs the running stack):** (a) that Next.js's rewrite proxy actually appends `X-Forwarded-For` — if it does not, `hops=1` falls back to the Next container address and the bucket is global again. The new `ip-isolation` drill detects exactly this (two different source addresses must not share a bucket). (b) the compose file publishes API port 8000 for development; a client reaching the API **directly** controls the last XFF entry. 
| Tier | Verdict | Condition / required action |
|---|---|---|
| Pilot | **ACCEPTED** only if `ip-isolation` passes | `TRUSTED_PROXY_HOPS` set to the true number of proxies; API port **not** published to the internet. |
| Public | **BLOCKER** until: `ip-isolation` + `ip-limit` drills pass in the production topology; deployment doc states hop count per topology; API reachable only via the proxy/ingress; ingress strips/overwrites client-supplied `X-Forwarded-For`. |

## 3. Session invalidation on password change
**Current state:** there is **no** user-facing password-change or reset endpoint. The only way a password changes is `app.cli set-password`, which revokes all of the user's sessions and audits the event (covered by the `session-revocation` drill — not yet run).
| Tier | Verdict | Condition |
|---|---|---|
| Pilot | **ACCEPTED** | Users cannot change passwords themselves; operator reset revokes sessions. |
| Public | **BLOCKER (by construction)** | Any future change-password / reset endpoint **must**, in the same transaction: update the hash, revoke *all other* sessions (and optionally issue a fresh one), write an audit event, and ship with a test proving an old cookie returns 401. Also add "sign out everywhere". A public product without self-service password change/reset is itself not launch-ready (see ADR A8). |

## 4. Account deletion / data erasure
**Current state:** not implemented. `users` FKs are `RESTRICT`, so even an operator cannot delete a user without first deleting their projects, jobs and sessions by hand; **no documented procedure exists.**
**Data held about a person:** email, password hash, sessions (user-agent string), projects (user-authored text), jobs, and audit rows. Audit rows are append-only by design and hold the user's UUID (`actor_id`) and, for failed logins, an **unsalted truncated SHA-256 of the email** (`email_fingerprint`) — a guessable pseudonym, i.e. still personal data. Retention and erasure treatment of both needs a legal decision (e.g. keep UUID-only pseudonymous records for a defined period, then purge via a controlled maintenance role; salt/HMAC the fingerprint).
| Tier | Verdict | Condition / required action |
|---|---|---|
| Pilot | **ACCEPTED with a required interim step** | Pilot users informed in writing; implement an operator `delete-user` CLI (cascade projects/sessions/jobs, audit event, confirmation) *before* the pilot starts, because today there is no way to honour a deletion request. |
| Public | **BLOCKER** | Self-service deletion + export; defined retention for audit rows; HMAC (keyed) instead of plain SHA-256 for `email_fingerprint`; purge stored objects (later phases); tests; privacy notice. |

## 5. Other items surfaced while reviewing (not requested, but launch-relevant)
| Item | Pilot | Public |
|---|---|---|
| **Audit immutability depends on the DB role.** Triggers stop UPDATE/DELETE/TRUNCATE by any role *except* one that can `DROP TRIGGER`; the app currently connects as the table owner. Use a separate migration/owner role and an app role with only INSERT/SELECT on `audit_events`. | ACCEPTED | BLOCKER |
| No email verification; registration reveals whether an email exists (409). | ACCEPTED | BLOCKER |
| No MFA. | ACCEPTED | Should-fix (risk-based) |
| Redis in compose is unauthenticated and ports are published. Dev only. Production needs auth/TLS/private network. | n/a (dev) | BLOCKER (config) |
| No vulnerability scanning, SBOM, hash-pinned installs, image digest pinning. | ACCEPTED | BLOCKER |
| TLS termination/HSTS, `COOKIE_SECURE=true` (enforced at startup in staging/production), secrets in a secret store. | REQUIRED | REQUIRED |
| Backups + a *tested restore* (spec §12). | REQUIRED | BLOCKER |
| Single embedded beat (`worker -B`): exactly one beat process in production or the sweep runs N times (safe due to SKIP LOCKED + CAS, but wasteful). | ACCEPTED | Fix: separate beat service |
| Fixed 7-day sessions, no idle timeout or session list. | ACCEPTED | Should-fix |
| `/docs` (Swagger) enabled when `APP_ENV != production`. | ACCEPTED | verify `APP_ENV=production` |

## Summary
- **Defects fixed by this review (unverified at runtime):** global/spoofable rate-limit client key; logout 204 pattern; unbounded broker connect time; missing index/constraint-name parity tests.
- **Public-launch blockers (9 rows marked BLOCKER above):** fail-open limiter; proxy topology proof; (future) password-change revocation; account deletion/export + retention; audit DB-role separation; email verification; Redis/prod config hardening; vuln scanning/pinning; TLS/backup-restore (as REQUIRED→BLOCKER).
- **Pilot prerequisites (3):** `ip-isolation` drill passes; operator `delete-user` exists; Redis-down alert.
