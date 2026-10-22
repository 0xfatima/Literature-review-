from alembic import command
from sqlalchemy import inspect, text

from app.db.session import get_engine
from tests.integration.conftest import alembic_cfg

EXPECTED = {"users", "auth_sessions", "projects", "audit_events", "jobs"}


def tables():
    return set(inspect(get_engine()).get_table_names()) - {"alembic_version"}


def test_upgrade_downgrade_upgrade_roundtrip():
    assert EXPECTED <= tables()
    command.downgrade(alembic_cfg(), "base")
    assert tables() == set()
    with get_engine().connect() as c:  # trigger function must be gone too
        assert c.scalar(text("SELECT count(*) FROM pg_proc WHERE proname='audit_events_block_mutation'")) == 0
    command.upgrade(alembic_cfg(), "head")
    assert EXPECTED <= tables()


def test_job_idempotency_indexes_are_partial_and_as_designed():
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='jobs'"))
        defs = {r[0]: r[1] for r in rows}
    active = defs["uq_jobs_active_fingerprint"]
    assert "UNIQUE" in active and "queued" in active and "running" in active
    assert "succeeded" not in active and "failed" not in active  # terminal states must not be constrained
    assert "request_key IS NOT NULL" in defs["uq_jobs_owner_request_key"]
    assert not any("project_id, type, idempotency_key" in d for d in defs.values())  # old permanent constraint is gone


def test_check_constraint_names_follow_convention_without_double_prefix():
    with get_engine().connect() as c:
        names = {r[0] for r in c.execute(text("SELECT conname FROM pg_constraint WHERE contype='c'"))}
    assert {"ck_users_status", "ck_projects_status", "ck_jobs_status", "ck_jobs_progress"} <= names


def test_models_match_migrated_schema_columns():
    from app.db import models
    insp = inspect(get_engine())
    for table in models.Base.metadata.sorted_tables:
        db_cols = {c["name"] for c in insp.get_columns(table.name)}
        assert {c.name for c in table.columns} == db_cols, table.name


def test_liveness_and_readiness(make_client):
    c = make_client()
    assert c.get("/healthz").json() == {"status": "ok"}
    r = c.get("/readyz")
    assert r.json()["checks"]["database"] == "ok"  # redis check depends on the CI/compose Redis service


def test_worker_health_reflects_celery_ping(make_client, monkeypatch):
    from app.jobs.celery_app import celery_app
    c = make_client()
    monkeypatch.setattr(celery_app.control, "ping", lambda timeout=1.0: [])
    assert c.get("/healthz/worker").status_code == 503
    monkeypatch.setattr(celery_app.control, "ping", lambda timeout=1.0: [{"celery@w1": {"ok": "pong"}}])
    r = c.get("/healthz/worker")
    assert r.status_code == 200 and r.json()["workers"] == ["celery@w1"]


def test_every_model_declared_index_exists_in_the_migrated_database():
    """The ORM index definitions (incl. the partial/expression jobs indexes) must construct AND match the migration."""
    from app.db import models
    insp = inspect(get_engine())
    for table in models.Base.metadata.sorted_tables:
        indexes = {i["name"] for i in insp.get_indexes(table.name)}
        uniques = {u["name"] for u in insp.get_unique_constraints(table.name)}
        in_db = indexes | uniques
        for idx in table.indexes:
            assert idx.name in in_db, f"{table.name}.{idx.name} declared in models but absent from migrated DB"


def test_every_model_declared_constraint_name_matches_the_migrated_database():
    """Guards the Alembic naming-convention interaction (no double 'ck_' prefixes, no unnamed drift)."""
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

    from app.db import models
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"))
        db_names = {r[0] for r in rows}
    for table in models.Base.metadata.sorted_tables:
        for con in table.constraints:
            if isinstance(con, (CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint)):
                assert con.name in db_names, f"{table.name}: constraint {con.name!r} not present in migrated DB"
