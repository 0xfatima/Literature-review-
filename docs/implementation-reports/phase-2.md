# Phase 2 report: accounts, projects and persistent chat (2026-10-23)

## Definition of done

> Two separate test users cannot access each other's projects, and a user's project and chat history persist after
> refreshing the browser or restarting the application.

| Criterion | Evidence (all actually executed) |
|---|---|
| Users cannot reach each other's data | `test_authorization_matrix.py`: every one of the 21 API operations, read from the app's own OpenAPI schema; 401 without a session, 404 for another user's ids, conversation ids invalid outside their own project. Mutation check: removing the project filter from the conversation lookup makes it fail. Browser: B sees "Project not found" and gets 404 on GET and POST of A's messages; A's history unchanged (verified with two separate browser processes locally; the spec runs in CI). |
| History persists across refresh | Playwright: reload keeps both messages, the thread and the URL. |
| ...and across sign-out / sign-in | Playwright: sign out, open the thread URL, sign in, land back on the same thread with its messages. |
| ...and across an application restart | Integration: a fresh app instance plus new login reads the history. Drill `restart-persistence`: api, web and proxy processes killed (0 ports listening) and restarted; the same session cookie and a fresh login both read both messages and the thread title. Run here against a native stack; runs against Docker in CI. |

## What was built

**Backend.** Migration `0002_conversations` (tables `conversations`, `messages`). `conversation_service` holds the
rules: auto-title from the first message (user-given titles never overwritten), gap-free `seq` per thread under a
row lock, idempotent send via `client_message_id`, optimistic-concurrency rename, archived projects read-only for
chat (history still readable), audit of thread create/rename/delete without any free text. Routes under
`/api/v1/projects/{project_id}/conversations[/{conversation_id}[/messages]]`; `GET /api/v1/jobs` (owner- and
project-scoped, `active=true` for non-terminal jobs). Already present from Phase 1 and re-verified: Argon2id
passwords, revocable server-side sessions, project create/rename/list/archive/delete, owner scoping.

**Frontend.** Dark research IDE: top bar, sidebar (project switcher, chat threads, Files), chat, workspace tabs
(Review status with stage track, Papers, Tables, Artifacts), status bar (live jobs with cancel, save state, errors).
Sidebar and chat resize by pointer or keyboard (WAI-ARIA splitter), collapse, and persist per browser. Thread and tab
are kept in the URL; sign-in returns to the requested page (open-redirect safe). Failed sends offer Retry with the
same client id, so the server stores the message once. All text colours meet WCAG AA (>= 4.5:1, computed).

**No simulated functionality.** The assistant is not connected; chat says so and never fabricates replies.
Papers/Tables/Artifacts/Files show empty states that explain what comes next. "Check background worker" enqueues a
real job whose progress the status bar shows.

## Defects found by running the browser suite (and fixed)

1. **Login redirect pointed at the web server's internal origin** (`localhost:3001` here, `web:3000` in Docker):
   two causes. (a) The middleware built its redirect from `req.url`. (b) In `infra/proxy/nginx.conf`, `location /`
   declared its own `proxy_set_header`, which in nginx discards **all** inherited ones, so Host/X-Forwarded-* never
   reached Next.js. Hidden in CI only because both ports happened to be 3000. Fixed: validated public origin from the
   forwarded headers; headers moved to server level; a unit test now forbids `proxy_set_header` in any location.
2. **Creating a project left the cached project list stale**: the list (and the workspace's project switcher) did not
   show the new project. Fixed by invalidating the list on create.
3. **Signing out from a project page redirected to `/login?next=...`** (the auth guard treated the deliberate
   sign-out as an expired session). Fixed with a full-page navigation that also drops the old session's cached data.

## Tests

| Suite | Result |
|---|---|
| Backend pytest (unit + integration, real PostgreSQL 16 / Redis 7, `REQUIRE_INTEGRATION=1`) | **132 passed, 0 skipped**, 84% line coverage (was 100 tests) |
| Stdlib-only runner (no virtualenv) | 39 passed |
| ruff, ruff format, mypy --strict, `alembic check`, up/down/up migrations | clean |
| Web: ESLint, Prettier, tsc, Vitest | clean, **27 passed** |
| `next build` | succeeded |
| Playwright, local (Chromium 131, one process per test) | **11 of 13 passed**; the 2 two-user specs cannot run in this sandbox's single-process Chromium (a second browser context does not keep cookies for navigations: probed, page `fetch` 200 vs navigation 401). Their behaviour was verified with two separate browser processes (see above). All 13 must pass in CI. |
| Drill `restart-persistence` | passed against the native stack |

Also added: a concurrency test (12 parallel sends; without the row lock 8 of 12 fail on duplicate `seq`), and
frontend/backend contract tests (stage list and message roles must match the backend).

## CI changes

E2E stack runs with `RATE_LIMIT_LOGIN_PER_MIN=100` (the suite signs in ~15-30 times a minute) and the drills get
`DRILL_LOGIN_LIMIT=100`; the Playwright gate now requires at least 13 passing tests; new drill `restart-persistence`.

## Migrations, configuration, deployment

`alembic upgrade head` applies `0002_conversations` (reversible). No new environment variables. The nginx config
change requires restarting the `proxy` service. `alembic.ini` gains `path_separator = os` (removes a warning).

## Known limitations

No assistant replies (later phase). No real-time push: the status bar polls (1.5 s while jobs run, else 10 s);
other tabs see new messages on reload. Message search, editing and deleting single messages are not implemented.
Thread drafts are not persisted. Narrow screens stack the panels rather than using drawers. API types are still
hand-written (contract tests cover the values the UI branches on). `next lint` is deprecated and Actions run on
Node 20 shims (both from Phase 1 notes).

## Manual verification

1. `docker compose up --build`, open http://localhost:3000, register.
2. Create a project, click **Start a thread**, send two messages (Enter sends, Shift+Enter adds a line).
3. Reload: same thread and messages. Drag or keyboard-resize the panels; reload: sizes kept.
4. Click **Check background worker**: the status bar shows progress, then "No background jobs running".
5. Sign out, paste the thread URL, sign in: back on the same thread.
6. `docker compose restart api web proxy`, reload: still there.
7. In a private window, register a second user and open the first user's URL: "Project not found".

## Recommended next phase

Protocol definition with the LangGraph workflow gate (persistent checkpoints, approval interrupt, resume after
restart), and the LLM gateway behind an interface with a deterministic fake for tests.
