"""Integration fixtures. Require a REAL PostgreSQL (TEST_DATABASE_URL). SQLite is not supported:
the schema uses JSONB, partial/expression unique indexes and plpgsql triggers."""

import os
import pathlib
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_dispatcher, get_rate_limiter
from app.core.rate_limit import InMemoryRateLimiter
from app.db.session import get_engine, get_sessionmaker
from app.main import create_app

API_DIR = pathlib.Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(config, items):
    """Without a real database every integration test is reported as SKIPPED (visible), never as passed."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    if os.environ.get("REQUIRE_INTEGRATION") == "1":
        # CI / validation runs: a missing database must FAIL the run, never silently skip.
        raise pytest.UsageError("REQUIRE_INTEGRATION=1 but TEST_DATABASE_URL is not set")
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set: integration test NOT RUN")
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


CSRF = {"X-SRS-CSRF": "1", "Origin": "http://localhost:3000"}


def alembic_cfg() -> Config:
    return Config(str(API_DIR / "alembic.ini"))


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    # This fixture DROPS SCHEMA public. Refuse unless the target database is unmistakably a test database,
    # so a TEST_DATABASE_URL accidentally pointed at a development/production database cannot wipe it.
    db_name = get_engine().url.database or ""
    if "test" not in db_name.lower():
        pytest.exit(f"Refusing to wipe database {db_name!r}: TEST_DATABASE_URL must name a database containing 'test'")
    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS lg_checkpoints CASCADE"))  # LangGraph checkpoints (Phase 3)
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_cfg(), "head")
    yield


class FakeDispatcher:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def __call__(self, task: str, job_id: str) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.append((task, job_id))


@pytest.fixture
def dispatcher() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def app(dispatcher):
    application = create_app()
    limiter = InMemoryRateLimiter()
    application.dependency_overrides[get_rate_limiter] = lambda: limiter
    application.dependency_overrides[get_dispatcher] = lambda: dispatcher
    return application


@pytest.fixture
def make_client(app):
    def _make() -> TestClient:
        return TestClient(app, headers=CSRF, base_url="http://localhost:8000")

    return _make


@pytest.fixture
def db():
    with get_sessionmaker()() as session:
        yield session


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.test"


PASSWORD = "a-long-test-password-1"


@pytest.fixture
def user_client(make_client):
    """Factory: returns (client, email, user_id) already registered and logged in."""

    def _make():
        c = make_client()
        email = new_email()
        r = c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
        return c, email, uid

    return _make


def drain(dispatcher, *, limit: int = 50) -> list[str]:
    """Run every workflow job the API has dispatched so far, in order, IN THIS PROCESS (real task body, real
    LangGraph, real Postgres checkpointer; only the broker hop is skipped). Returns the task outcomes."""
    from app.jobs import tasks

    outcomes = []
    for _ in range(limit):
        pending = [jid for task, jid in dispatcher.sent if task == "workflow.advance"]
        if not pending:
            return outcomes
        dispatcher.sent = [s for s in dispatcher.sent if s[0] != "workflow.advance"]
        outcomes += [tasks.workflow_advance(jid) for jid in pending]
    raise AssertionError("workflow kept dispatching jobs: possible loop")
