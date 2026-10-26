#!/usr/bin/env python3
"""Representative concurrent workload for Phase 15. Measures; never assumes.

What is REAL: the FastAPI app and every route (one TestClient per simulated user, all concurrent), PostgreSQL, Redis
(rate limiters, provider throttles, the cross-process AI semaphore), the job admission pump, the Celery TASK FUNCTIONS
(app.jobs.tasks.*, run by a pool of worker threads exactly as a worker would run them), real HTTP to the providers,
real PDF parsing in resource-limited child processes, real file storage.
What is NOT: the Celery broker hop (an in-process queue stands in; CI's e2e job covers the real broker) and the
providers themselves (scripts/loadtest/mock_providers.py speaks their documented formats; records are synthetic).
Request latency excludes network/TLS/proxy time (in-process ASGI), so production adds those on top.

  PYTHONPATH=services/api python scripts/loadtest/run.py --users 6 --out validation-evidence/loadtest.json
Needs PostgreSQL + Redis. Uses (and RESETS) the database in LOADTEST_DATABASE_URL, whose name must contain "test".
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import statistics
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
API = ROOT / "services" / "api"
sys.path[:0] = [str(API), str(HERE)]

import mock_providers  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--users", type=int, default=6)
ap.add_argument("--workers", type=int, default=6, help="job worker threads (a worker pool)")
ap.add_argument("--openalex", type=int, default=2000)
ap.add_argument("--epmc", type=int, default=1500)
ap.add_argument("--overlap", type=int, default=500)
ap.add_argument("--pdfs", type=int, default=8, help="PDFs uploaded per user")
ap.add_argument("--decisions", type=int, default=200, help="single screening decisions per user")
ap.add_argument("--reads", type=int, default=15, help="read-mix iterations per user")
ap.add_argument("--reports", type=int, default=3, help="report generations per user")
ap.add_argument("--llm-global", type=int, default=3, help="LLM_GLOBAL_MAX_CONCURRENT_CALLS under test")
ap.add_argument("--rate-limit-every", type=int, default=25, help="mock answers 429 to every n-th request (0=never)")
ap.add_argument("--only-search", action="store_true", help="stop after W1 (ingestion profiling)")
ap.add_argument("--out", default="validation-evidence/loadtest.json")
args = ap.parse_args()

CFG = mock_providers.MockConfig(
    openalex_records=args.openalex,
    epmc_records=args.epmc,
    overlap=args.overlap,
    rate_limit_every=args.rate_limit_every,
)
SRV, MOCK = mock_providers.start(CFG)
BASE = f"http://127.0.0.1:{SRV.server_address[1]}"
STORE = tempfile.mkdtemp(prefix="srs-load-store-")
DB_URL = os.environ.get("LOADTEST_DATABASE_URL", "postgresql+psycopg://srs:srs_ci@localhost:5432/srs_load_test")
os.environ.update(
    APP_ENV="test",
    LOG_LEVEL="WARNING",
    DATABASE_URL=DB_URL,
    REDIS_URL=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    SEARCH_PROVIDERS_ENABLED="openalex,europepmc,arxiv",
    SEARCH_MAX_RECORDS_PER_PROVIDER=str(max(args.openalex, args.epmc)),
    SEARCH_PAGE_SIZE="200",
    LLM_PROVIDER="gemini",
    LLM_API_KEY="mock-key-not-secret",
    LLM_MODEL="mock-model",
    LLM_BASE_URL=f"{BASE}/gemini",
    LLM_SEND_PRIVATE_DOCUMENTS="true",
    LLM_TERMS_RECORD_VERSION="loadtest",
    LLM_TERMS_REVIEWED_ON=date.today().isoformat(),
    LLM_MIN_INTERVAL_S="0",
    LLM_MAX_CONCURRENT_CALLS="16",  # per process: deliberately high so ONLY the shared limit can hold the line
    LLM_GLOBAL_MAX_CONCURRENT_CALLS=str(args.llm_global),
    STORAGE_LOCAL_ROOT=STORE,
    RATE_LIMIT_SEARCH_PER_HOUR="1000",
    RATE_LIMIT_UPLOAD_PER_HOUR="10000",
    RATE_LIMIT_EXTRACTION_PER_HOUR="1000",
    RATE_LIMIT_WRITES_PER_MIN="100000",
    RATE_LIMIT_REGISTER_PER_HOUR="1000",
    RATE_LIMIT_LOGIN_PER_MIN="1000",
)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.api.deps import get_dispatcher, get_rate_limiter  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.rate_limit import InMemoryRateLimiter  # noqa: E402
from app.db.models import Job, LlmConsent  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.jobs import tasks  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import consent_service, job_service  # noqa: E402
from tests.fixtures.pdfs import PARA, make_pdf  # noqa: E402

CSRF = {"X-SRS-CSRF": "1", "Origin": "http://localhost:3000"}
PASSWORD = "a-long-loadtest-password-1"
TASKS = {
    "workflow.advance": tasks.workflow_advance,
    "search.run": tasks.search_run,
    "document.process": tasks.document_process,
    "extraction.run": tasks.extraction_run,
    "relevance.assess": tasks.relevance_assess,
    "appraisal.suggest": tasks.appraisal_suggest,
    "synthesis.draft": tasks.synthesis_draft,
    "pipeline.advance": tasks.pipeline_advance,
    "system.ping": tasks.system_ping,
}

# ---------------------------------------------------------------- broker + worker pool (stands in for Celery)
BROKER: queue.Queue[tuple[str, str] | None] = queue.Queue()
EXEC_ERRORS: list[str] = []


def broker_put(task: str, job_id: str) -> None:
    BROKER.put((task, job_id))


def worker() -> None:
    while (item := BROKER.get()) is not None:
        task, jid = item
        try:
            TASKS[task](jid)
        except Exception as e:  # noqa: BLE001 - recorded and reported, never hidden
            EXEC_ERRORS.append(f"{task}: {type(e).__name__}: {e}")
        with get_sessionmaker()() as db:  # what the Celery task_postrun hook does
            job_service.pump(db, get_settings(), broker_put, wait_for_lock=False)


def pumper(stop: threading.Event) -> None:
    while not stop.wait(1.0):  # the beat-driven pump
        with get_sessionmaker()() as db:
            job_service.pump(db, get_settings(), broker_put)


# ---------------------------------------------------------------- SQL accounting (statements and DB time)
SQL = {"n": 0, "s": 0.0}


def _count_sql() -> None:
    from sqlalchemy import event

    @event.listens_for(get_engine(), "before_cursor_execute")
    def _b(conn, *_a):  # type: ignore[no-untyped-def]
        conn.info.setdefault("t0", []).append(time.perf_counter())

    @event.listens_for(get_engine(), "after_cursor_execute")
    def _a(conn, *_a):  # type: ignore[no-untyped-def]
        d = time.perf_counter() - conn.info["t0"].pop()
        with LOCK:
            SQL["n"] += 1
            SQL["s"] += d


# ---------------------------------------------------------------- latency capture
LAT: dict[str, list[float]] = defaultdict(list)
STATUS: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
LOCK = threading.Lock()


_LABEL = threading.local()


class TimedClient(TestClient):
    """Records the latency of every request; `labelled(...)` names a request so templated URLs aggregate."""

    def request(self, method: str, url: Any, *a: Any, **kw: Any):  # type: ignore[override]
        t0 = time.perf_counter()
        r = super().request(method, url, *a, **kw)
        label = getattr(_LABEL, "value", None)
        key = label or f"{method} {str(url).split('?')[0]}"
        with LOCK:
            LAT[key].append(time.perf_counter() - t0)
            STATUS[key][r.status_code] += 1
        return r


def labelled(c: TestClient, method: str, url: str, label: str, **kw: Any):
    _LABEL.value = label
    try:
        return c.request(method, url, **kw)
    finally:
        _LABEL.value = None


class SwitchDispatcher:
    """FakeDispatcher-compatible (helpers read/replace `.sent`) during sequential setup; the shared broker after."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False
        self.live = False

    def __call__(self, task: str, job_id: str) -> None:
        if self.live:
            broker_put(task, job_id)
        else:
            self.sent.append((task, job_id))


def new_user() -> tuple[TimedClient, SwitchDispatcher, str]:
    disp = SwitchDispatcher()
    app = create_app()
    lim = InMemoryRateLimiter()
    app.dependency_overrides[get_rate_limiter] = lambda: lim
    app.dependency_overrides[get_dispatcher] = lambda: disp
    c = TimedClient(app, headers=CSRF, base_url="http://localhost:8000")
    email = f"load-{uuid.uuid4().hex[:10]}@example.test"
    r = c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201, r.text
    assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return c, disp, r.json()["id"]


def poll(fn, done, timeout: float, every: float = 0.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = fn()
        if done(v):
            return v, time.monotonic() - t0
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout}s; last={str(v)[:300]}")


def run_concurrently(fn, items) -> list[Any]:
    out: list[Any] = [None] * len(items)
    errs: list[BaseException] = []

    def go(i: int, it: Any) -> None:
        try:
            out[i] = fn(it)
        except BaseException as e:  # noqa: BLE001
            errs.append(e)

    th = [threading.Thread(target=go, args=(i, it)) for i, it in enumerate(items)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    if errs:
        raise errs[0]
    return out


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def job_stats(since: datetime, types: tuple[str, ...]) -> dict[str, Any]:
    with get_sessionmaker()() as db:
        rows = db.execute(
            select(Job.type, Job.status, Job.created_at, Job.started_at, Job.finished_at, Job.owner_id).where(
                Job.created_at >= since, Job.type.in_(types)
            )
        ).all()
    out: dict[str, Any] = {}
    for t in types:
        rs = [r for r in rows if r[0] == t]
        if not rs:
            continue
        qd = [(r[3] - r[2]).total_seconds() for r in rs if r[3]]
        du = [(r[4] - r[3]).total_seconds() for r in rs if r[3] and r[4]]
        out[t] = {
            "jobs": len(rs),
            "statuses": {s: sum(1 for r in rs if r[1] == s) for s in {r[1] for r in rs}},
            "queue_delay_s": {"p50": pct(qd, 0.5), "p95": pct(qd, 0.95), "max": max(qd, default=float("nan"))},
            "duration_s": {"p50": pct(du, 0.5), "p95": pct(du, 0.95), "max": max(du, default=float("nan"))},
        }
    return out


def lat_summary(prefix_filter=None) -> dict[str, Any]:
    out = {}
    for k, xs in sorted(LAT.items()):
        if prefix_filter and not prefix_filter(k):
            continue
        out[k] = {
            "n": len(xs),
            "p50_ms": round(pct(xs, 0.5) * 1000, 1),
            "p95_ms": round(pct(xs, 0.95) * 1000, 1),
            "max_ms": round(max(xs) * 1000, 1),
            "status": dict(STATUS[k]),
        }
    return out


# ---------------------------------------------------------------- main
def reset_db() -> None:
    name = get_engine().url.database or ""
    if "test" not in name.lower():
        sys.exit(f"refusing to reset {name!r}: LOADTEST_DATABASE_URL must name a database containing 'test'")
    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS lg_checkpoints CASCADE"))
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    command.upgrade(Config(str(API / "alembic.ini")), "head")


def main() -> int:  # noqa: PLR0915 - a linear script reads best top to bottom
    import redis

    redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    reset_db()
    from tests.integration.support import approved
    from tests.integration.test_reports import accepted_project

    results: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "params": vars(args),
        "limits": {
            k: getattr(get_settings(), k)
            for k in ("job_max_inflight_per_user", "job_max_inflight_global", "llm_global_max_concurrent_calls")
        },
    }
    stop = threading.Event()
    pool = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in pool:
        t.start()
    threading.Thread(target=pumper, args=(stop,), daemon=True).start()

    # ---- setup (sequential, not measured): approved protocols, and small fully-accepted reviews for reports
    t0 = time.monotonic()
    users = [new_user() for _ in range(args.users)]
    pids = [approved(c, d) for c, d, _ in users]
    from app.providers.storage import LocalObjectStore

    store = LocalObjectStore(pathlib.Path(STORE))

    class _Factory:  # the shape the integration helpers expect from the user_client fixture
        def __init__(self, c, uid):
            self.c, self.uid = c, uid

        def __call__(self):
            return self.c, "x", self.uid

    report_projects = []
    for c, d, uid in users:
        with get_sessionmaker()() as db:
            _, rpid, *_ = accepted_project(_Factory(c, uid), d, store, db)
        report_projects.append(rpid)
    for _, d, _ in users:
        d.live = True
    # From here on the real adapters talk to the mock over HTTP (setup used the fixture transport, routed by host).
    os.environ["SCHOLARLY_ENDPOINTS"] = f"openalex={BASE}/openalex/works,europepmc={BASE}/europepmc/search"
    get_settings.cache_clear()
    LAT.clear()
    STATUS.clear()
    results["setup_s"] = round(time.monotonic() - t0, 1)

    # ---- W1 scholarly search: every user searches at once
    _count_sql()
    SQL.update(n=0, s=0.0)
    since = datetime.now(UTC)
    t0 = time.monotonic()

    def search(i: int) -> dict[str, Any]:
        c, pid = users[i][0], pids[i]
        r = c.post(f"/api/v1/projects/{pid}/search-runs", json={"providers": ["openalex", "europepmc"]})
        assert r.status_code == 202, r.text
        rid = r.json()["id"]
        run, took = poll(
            lambda: labelled(c, "GET", f"/api/v1/projects/{pid}/search-runs/{rid}", "GET search-run (poll)").json(),
            lambda v: v["status"] not in ("queued", "running"),
            timeout=900,
        )
        return {"status": run["status"], "seconds": round(took, 1), "papers": c.get(
            f"/api/v1/projects/{pid}/papers?limit=1").json()["total"]}

    per_user = run_concurrently(search, list(range(args.users)))
    results["w1_search"] = {
        "wall_s": round(time.monotonic() - t0, 1),
        "per_user": per_user,
        "expected_unique_papers_per_project": args.openalex + args.epmc - args.overlap,
        "jobs": job_stats(since, ("search.run",)),
        "provider": MOCK.snapshot(),
        "sql_statements": SQL["n"],
        "sql_seconds": round(SQL["s"], 1),
    }
    if args.only_search:
        stop.set()
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, default=str))
        print(json.dumps(results["w1_search"], indent=2, default=str))
        return 0

    # ---- W2 read mix on the large projects, all users at once
    def reads(i: int) -> None:
        c, pid = users[i][0], pids[i]
        total = c.get(f"/api/v1/projects/{pid}/papers?limit=1").json()["total"]
        for k in range(args.reads):
            labelled(c, "GET", f"/api/v1/projects/{pid}/papers?limit=25&offset=0", "GET papers (page 1)")
            labelled(c, "GET", f"/api/v1/projects/{pid}/papers?limit=25&offset={max(0, total - 25 - k)}", "GET papers (last page)")
            labelled(c, "GET", f"/api/v1/projects/{pid}/screening/queue?limit=25", "GET screening queue")
            labelled(c, "GET", f"/api/v1/projects/{pid}/prisma/flow", "GET prisma flow")
            labelled(c, "GET", f"/api/v1/projects/{pid}/search/summary", "GET search summary")
            labelled(c, "GET", f"/api/v1/projects/{pid}/audit-events?limit=50", "GET audit events")
            labelled(c, "GET", f"/api/v1/jobs?project_id={pid}&limit=20", "GET jobs")

    t0 = time.monotonic()
    run_concurrently(reads, list(range(args.users)))
    results["w2_reads"] = {"wall_s": round(time.monotonic() - t0, 1), "latency": lat_summary(lambda k: k.startswith("GET ") and "poll" not in k)}
    LAT.clear()
    STATUS.clear()

    # ---- W3 screening writes
    def screen(i: int) -> list[str]:
        c, pid = users[i][0], pids[i]
        items = c.get(f"/api/v1/projects/{pid}/papers?limit=100&offset=0").json()["items"]
        items += c.get(f"/api/v1/projects/{pid}/papers?limit=100&offset=100").json()["items"]
        ids = [p["id"] for p in items][: args.decisions]
        for n, p in enumerate(ids):
            labelled(
                c,
                "POST",
                f"/api/v1/projects/{pid}/screening/papers/{p}/decision",
                "POST screening decision",
                json={"decision": "include" if n < args.pdfs else "uncertain"},
            )
        return ids

    t0 = time.monotonic()
    screened = run_concurrently(screen, list(range(args.users)))
    results["w3_screening"] = {"wall_s": round(time.monotonic() - t0, 1), "latency": lat_summary()}
    LAT.clear()
    STATUS.clear()

    # ---- W4 PDF parsing: every user uploads PDFs at once (fair admission is exercised here)
    since = datetime.now(UTC)
    t0 = time.monotonic()

    def pdfs(i: int) -> float:
        c, pid = users[i][0], pids[i]
        start = time.monotonic()
        for n, paper_id in enumerate(screened[i][: args.pdfs]):
            pages = [f"This RCT enrolled {100 + n} adult participants. " + PARA.format(n=40) * 4] + [
                PARA.format(n=60) * 5 for _ in range(4)
            ]
            r = labelled(
                c,
                "POST",
                f"/api/v1/projects/{pid}/documents?paper_id={paper_id}",
                "POST document upload",
                content=make_pdf(pages),
                headers={"Content-Type": "application/pdf", "X-Filename": f"paper-{n}.pdf"},
            )
            assert r.status_code == 202, r.text
        poll(
            lambda: labelled(c, "GET", f"/api/v1/projects/{pid}/documents?limit=100", "GET documents (poll)").json()["items"],
            lambda items: len(items) == args.pdfs and all(d["parse_status"] not in ("queued", "processing") for d in items),
            timeout=900,
        )
        return round(time.monotonic() - start, 1)

    per_user_pdf = run_concurrently(pdfs, list(range(args.users)))
    results["w4_pdf"] = {
        "wall_s": round(time.monotonic() - t0, 1),
        "per_user_completion_s": per_user_pdf,
        "jobs": job_stats(since, ("document.process",)),
        "upload_latency": lat_summary(lambda k: k == "POST document upload"),
    }
    LAT.clear()
    STATUS.clear()

    # ---- W5 AI extraction with the REAL Gemini adapter against the mock: the shared concurrency limit must hold
    fields = [
        {"field_key": "n", "label": "Sample size", "type": "number"},
        {"field_key": "design", "label": "Design", "type": "choice", "options": ["RCT", "Cohort"]},
    ]
    sids = []
    for (c, _, uid), pid in zip(users, pids, strict=True):
        sid = c.post(f"/api/v1/projects/{pid}/extraction/schemas", json={"field_definitions": fields}).json()["id"]
        assert c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/approve", json={}).status_code == 200
        with get_sessionmaker()() as db:
            db.add(LlmConsent(project_id=uuid.UUID(pid), user_id=uuid.UUID(uid), provider="gemini", model="mock-model",
                              data_class="private_document", terms_record_version="loadtest",
                              notice_version=consent_service.NOTICE_VERSION))
            db.commit()
        sids.append(sid)
    gemini_before = MOCK.snapshot()["requests"].get("gemini", 0)
    MOCK.peak_inflight["gemini"] = 0
    since = datetime.now(UTC)
    t0 = time.monotonic()

    def extract(i: int) -> dict[str, Any]:
        c, pid, sid = users[i][0], pids[i], sids[i]
        r = c.post(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/runs", json={})
        assert r.status_code == 202, r.text
        jid = r.json()["job_id"]
        job, took = poll(lambda: c.get(f"/api/v1/jobs/{jid}").json(), lambda j: j["status"] not in ("queued", "running"), 900)
        rows = c.get(f"/api/v1/projects/{pid}/extraction/schemas/{sid}/table?limit=200").json()["rows"]
        found = sum(1 for row in rows for cell in row["cells"].values() if cell.get("status") == "found")
        return {"status": job["status"], "seconds": round(took, 1), "values_found": found}

    per_user_ex = run_concurrently(extract, list(range(args.users)))
    snap = MOCK.snapshot()
    results["w5_extraction"] = {
        "wall_s": round(time.monotonic() - t0, 1),
        "per_user": per_user_ex,
        "jobs": job_stats(since, ("extraction.run",)),
        "ai_requests": snap["requests"].get("gemini", 0) - gemini_before,
        "ai_peak_concurrent_requests_seen_by_provider": snap["peak_concurrent_requests"].get("gemini"),
        "ai_shared_limit": args.llm_global,
        "worker_threads": args.workers,
    }

    # ---- W6 report generation (synchronous route) on the accepted reviews, all users at once
    def report(i: int) -> None:
        c, pid = users[i][0], report_projects[i]
        for _ in range(args.reports):
            r = labelled(c, "POST", f"/api/v1/projects/{pid}/reports", "POST report generate", json={"note": "load"})
            assert r.status_code == 201, r.text
            labelled(c, "POST", f"/api/v1/projects/{pid}/reports/{r.json()['id']}/exports", "POST report export docx", json={"format": "docx"})

    LAT.clear()
    STATUS.clear()
    t0 = time.monotonic()
    run_concurrently(report, list(range(args.users)))
    results["w6_reports"] = {"wall_s": round(time.monotonic() - t0, 1), "latency": lat_summary()}

    # ---- isolation: nobody sees anybody else's data
    leaks = []
    for i, (c, _, uid) in enumerate(users):
        own = {p["id"] for p in c.get("/api/v1/projects?limit=100").json()["items"]}
        if own != {pids[i], report_projects[i]}:
            leaks.append(f"user {i} lists {own}")
        for j, other in enumerate(pids):
            if j != i and c.get(f"/api/v1/projects/{other}/papers?limit=1").status_code != 404:
                leaks.append(f"user {i} can read project of user {j}")
    with get_sessionmaker()() as db:
        cross = db.scalar(text(
            "SELECT count(*) FROM paper_documents d JOIN paper_records p ON p.id = d.paper_id "
            "WHERE d.paper_id IS NOT NULL AND d.project_id <> p.project_id"))
        foreign_audit = db.scalar(text(
            "SELECT count(*) FROM audit_events a JOIN projects p ON p.id = a.project_id "
            "WHERE a.actor_id IS NOT NULL AND a.actor_id <> p.owner_id"))
    results["isolation"] = {"leaks": leaks, "documents_linked_across_projects": cross,
                            "audit_events_by_non_owner_in_project": foreign_audit}
    results["job_execution_errors"] = EXEC_ERRORS
    stop.set()
    for _ in pool:
        BROKER.put(None)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps(results, indent=2, default=str))
    ok = not leaks and not cross and not foreign_audit and not EXEC_ERRORS
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
