"""Phase 14: audit log API, audit attribution, HTTP hardening, session retention (real PostgreSQL)."""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.core.context import reset_actor_process, set_actor_process
from app.db.models import AuditEvent, AuthSession
from app.services import audit_service, auth_service


def _project(c, title="Hardening"):
    r = c.post("/api/v1/projects", json={"title": title, "topic": "topic"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_owner_reads_project_audit_log_newest_first_with_paging(user_client):
    c, _, uid = user_client()
    pid = _project(c)
    for i in range(3):
        c.patch(f"/api/v1/projects/{pid}", json={"expected_version": i + 1, "title": f"t{i}"})
    page1 = c.get(f"/api/v1/projects/{pid}/audit-events", params={"limit": 2}).json()
    assert [e["action"] for e in page1["items"]] == ["project.updated", "project.updated"]
    me = {"kind": "user", "is_you": True, "process": None}
    assert page1["next_cursor"] and all(e["actor"] == me for e in page1["items"])
    page2 = c.get(f"/api/v1/projects/{pid}/audit-events", params={"limit": 2, "cursor": page1["next_cursor"]}).json()
    assert [e["action"] for e in page2["items"]] == ["project.updated", "project.created"]
    assert page2["next_cursor"] is None
    assert not {e["id"] for e in page1["items"]} & {e["id"] for e in page2["items"]}
    only = c.get(f"/api/v1/projects/{pid}/audit-events", params={"category": "project", "actor": "user"}).json()
    assert len(only["items"]) == 4 and all(e["category"] == "project" for e in only["items"])


def test_audit_log_is_private_and_read_only(user_client):
    a, _, _ = user_client()
    b, _, _ = user_client()
    pid = _project(a)
    assert b.get(f"/api/v1/projects/{pid}/audit-events").status_code == 404
    assert b.get(f"/api/v1/projects/{pid}/audit-events/export.csv").status_code == 404
    assert a.delete(f"/api/v1/projects/{pid}/audit-events").status_code == 405
    assert a.patch(f"/api/v1/projects/{pid}/audit-events", json={}).status_code == 405


def test_invalid_filters_and_cursor_are_rejected(user_client):
    c, _, _ = user_client()
    pid = _project(c)
    for params in ({"category": "nope"}, {"actor": "robot"}, {"cursor": "garbage!"}, {"entity_type": "a;b"}):
        r = c.get(f"/api/v1/projects/{pid}/audit-events", params=params)
        assert r.status_code == 422, (params, r.text)


def test_csv_export_is_itself_audited_and_has_no_free_text(user_client, db):
    c, _, uid = user_client()
    pid = _project(c, title="Secret title ZZZ")
    r = c.get(f"/api/v1/projects/{pid}/audit-events/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "ZZZ" not in r.text
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [row["action"] for row in rows] == ["project.created"]  # the export event is committed after the read
    assert rows[0]["actor"] == "you"
    exported = db.scalars(select(AuditEvent).where(AuditEvent.action == "audit.exported")).all()
    assert any(str(e.project_id) == pid and str(e.actor_id) == uid for e in exported)


def test_account_log_shows_own_sign_ins_only(user_client):
    a, _, uid_a = user_client()
    b, _, _ = user_client()
    actions_a = [e["action"] for e in a.get("/api/v1/auth/me/audit-events").json()["items"]]
    assert actions_a[:2] == ["auth.login_succeeded", "user.registered"]
    ids_b = {e["entity_id"] for e in b.get("/api/v1/auth/me/audit-events").json()["items"]}
    assert uid_a not in ids_b


def test_system_events_are_attributed_to_the_process(db):
    token = set_actor_process("worker:system.test_task")
    try:
        ev = audit_service.record(db, action="document.purged", entity_type="document", entity_id=None, actor_id=None)
        db.commit()
    finally:
        reset_actor_process(token)
    assert db.get(AuditEvent, ev.id).actor_process == "worker:system.test_task"


def test_security_headers_on_api_responses(user_client):
    c, _, _ = user_client()
    r = c.get("/api/v1/projects")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and "default-src 'none'" in r.headers["content-security-policy"]


def test_oversized_body_is_rejected_before_the_route(user_client, set_settings):
    c, _, _ = user_client()
    set_settings(max_request_body_bytes=200)
    r = c.post("/api/v1/projects", json={"title": "x", "topic": "y" * 500})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_per_session_write_limit(user_client, set_settings):
    c, _, _ = user_client()
    other, _, _ = user_client()
    set_settings(rate_limit_writes_per_min=3)
    codes = [c.post("/api/v1/projects", json={"title": f"p{i}", "topic": "t"}).status_code for i in range(4)]
    assert codes == [201, 201, 201, 429]
    assert c.get("/api/v1/projects").status_code == 200  # reads are not limited by this
    assert other.post("/api/v1/projects", json={"title": "o", "topic": "t"}).status_code == 201  # per session


def test_ended_sessions_are_purged_after_retention(user_client, db):
    c, _, uid = user_client()
    c.post("/api/v1/auth/logout")
    uid = uuid.UUID(uid)
    assert db.scalars(select(AuthSession).where(AuthSession.user_id == uid)).all()
    later = datetime.now(UTC) + timedelta(days=get_settings().session_retention_days + 1)
    auth_service.purge_sessions(db, get_settings(), now=later)
    db.expire_all()
    assert db.scalars(select(AuthSession).where(AuthSession.user_id == uid)).all() == []
    # the audit trail of the session is untouched
    assert db.scalars(select(AuditEvent).where(AuditEvent.entity_id == uid, AuditEvent.action == "auth.logout")).all()


def test_deleting_a_project_removes_its_workflow_checkpoints(user_client, dispatcher, db):
    from sqlalchemy import text

    from tests.integration.test_protocol_workflow import project, started

    c, _, _ = user_client()
    pid = project(c)
    run = started(c, dispatcher, pid)
    thread = run.get("thread_id") or str(
        db.execute(text("SELECT thread_id FROM workflow_runs WHERE project_id = :p"), {"p": pid}).scalar_one()
    )
    count = text("SELECT count(*) FROM lg_checkpoints.checkpoints WHERE thread_id = :t")
    assert db.execute(count, {"t": thread}).scalar_one() > 0
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 204
    db.rollback()
    assert db.execute(count, {"t": thread}).scalar_one() == 0
