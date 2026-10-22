import json
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import AuditEvent
from tests.integration.conftest import PASSWORD, new_email


def events(db, **filters):
    q = select(AuditEvent).order_by(AuditEvent.at)
    for k, v in filters.items():
        q = q.where(getattr(AuditEvent, k) == v)
    return list(db.scalars(q))


def test_registration_login_logout_are_audited(make_client, db):
    c, email = make_client(), new_email()
    uid = uuid.UUID(c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD}).json()["id"])
    c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-123"})
    c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    c.post("/api/v1/auth/logout")
    actions = [e.action for e in events(db, entity_id=uid)]
    assert actions == ["user.registered", "auth.login_failed", "auth.login_succeeded", "auth.logout"]


def test_failed_login_audit_has_no_raw_email_or_password(make_client, db):
    c, email = make_client(), new_email()
    c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-123"})
    dump = json.dumps([[e.action, e.before, e.after] for e in events(db, action="auth.login_failed")])
    assert email not in dump and "wrong-password-123" not in dump and "email_fingerprint" in dump


def test_project_lifecycle_audited_without_free_text(user_client, db):
    c, _, uid = user_client()
    title, topic = "Secret title ZZZ", "Secret topic QQQ"
    pid = c.post("/api/v1/projects", json={"title": title, "topic": topic}).json()["id"]
    c.patch(f"/api/v1/projects/{pid}", json={"expected_version": 1, "title": "Another secret ZZZ2"})
    c.patch(f"/api/v1/projects/{pid}", json={"expected_version": 2, "archived": True})
    c.delete(f"/api/v1/projects/{pid}")
    evs = events(db, project_id=uuid.UUID(pid))
    assert [e.action for e in evs] == ["project.created", "project.updated", "project.archived", "project.deleted"]
    assert all(str(e.actor_id) == uid for e in evs)
    blob = json.dumps([[e.before, e.after, e.reason] for e in evs])
    for forbidden in ("ZZZ", "QQQ", "Secret"):
        assert forbidden not in blob
    assert evs[1].after["changed_fields"] == ["title"]


def test_events_survive_project_deletion_and_carry_correlation_id(user_client, db):
    c, _, _ = user_client()
    pid = c.post("/api/v1/projects", json={"title": "t", "topic": "x"}).json()["id"]
    c.delete(f"/api/v1/projects/{pid}", headers={"X-Correlation-ID": "audit-corr-0001"})
    deleted = events(db, project_id=uuid.UUID(pid), action="project.deleted")
    assert len(deleted) == 1 and deleted[0].correlation_id == "audit-corr-0001"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_events SET action = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
def test_audit_table_is_append_only_at_the_database_level(db, sql, make_client):
    make_client().post("/api/v1/auth/register", json={"email": new_email(), "password": PASSWORD})  # ensure rows exist
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text(sql))
        db.commit()
    db.rollback()
