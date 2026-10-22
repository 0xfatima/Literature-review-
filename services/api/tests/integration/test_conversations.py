"""Chat threads and message history against a real PostgreSQL."""

import threading
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditEvent, Message
from app.db.session import get_sessionmaker
from app.main import create_app
from app.services import conversation_service

from .conftest import CSRF, PASSWORD


def project(c, title="Review"):
    r = c.post("/api/v1/projects", json={"title": title, "topic": "Does X improve Y?"})
    assert r.status_code == 201, r.text
    return r.json()


def thread(c, pid, title=None):
    r = c.post(f"/api/v1/projects/{pid}/conversations", json={"title": title} if title else {})
    assert r.status_code == 201, r.text
    return r.json()


def send(c, pid, cid, content, client_id=None):
    body = {"content": content}
    if client_id:
        body["client_message_id"] = client_id
    return c.post(f"/api/v1/projects/{pid}/conversations/{cid}/messages", json=body)


def test_create_thread_defaults_and_first_message_sets_auto_title(user_client):
    c, _, _ = user_client()
    pid = project(c)["id"]
    t = thread(c, pid)
    assert t["title"] == "New conversation" and t["title_source"] == "auto" and t["message_count"] == 0
    r = send(c, pid, t["id"], "  Does exercise reduce depression in adults?  ")
    assert r.status_code == 201
    body = r.json()
    assert body["message"]["seq"] == 1 and body["message"]["role"] == "user"
    assert body["message"]["content"] == "Does exercise reduce depression in adults?"
    assert body["conversation"]["title"] == "Does exercise reduce depression in adults?"
    assert body["conversation"]["message_count"] == 1 and body["conversation"]["last_message_at"]
    send(c, pid, t["id"], "A second message must not retitle the thread")
    assert c.get(f"/api/v1/projects/{pid}/conversations/{t['id']}").json()["title"].startswith("Does exercise")


def test_user_given_title_is_never_overwritten_by_messages(user_client):
    c, _, _ = user_client()
    pid = project(c)["id"]
    t = thread(c, pid, "Search planning")
    assert t["title_source"] == "user"
    assert send(c, pid, t["id"], "hello").json()["conversation"]["title"] == "Search planning"


def test_messages_are_ordered_by_seq_and_paginate_both_ways(user_client):
    c, _, _ = user_client()
    pid = project(c)["id"]
    cid = thread(c, pid)["id"]
    for i in range(1, 8):
        assert send(c, pid, cid, f"m{i}").status_code == 201
    base = f"/api/v1/projects/{pid}/conversations/{cid}/messages"
    latest = c.get(f"{base}?limit=3").json()
    assert [m["content"] for m in latest["items"]] == ["m5", "m6", "m7"] and latest["has_older"] is True
    older = c.get(f"{base}?limit=3&before_seq=5").json()
    assert [m["content"] for m in older["items"]] == ["m2", "m3", "m4"] and older["has_older"] is True
    oldest = c.get(f"{base}?limit=3&before_seq=2").json()
    assert [m["content"] for m in oldest["items"]] == ["m1"] and oldest["has_older"] is False
    newer = c.get(f"{base}?after_seq=6").json()
    assert [m["content"] for m in newer["items"]] == ["m7"]
    assert [m["seq"] for m in c.get(base).json()["items"]] == list(range(1, 8))


def test_retried_send_with_same_client_id_is_stored_once(user_client, db):
    c, _, _ = user_client()
    pid = project(c)["id"]
    cid = thread(c, pid)["id"]
    key = str(uuid.uuid4())
    first = send(c, pid, cid, "only once", key)
    again = send(c, pid, cid, "only once", key)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["message"]["id"] == again.json()["message"]["id"]
    rows = db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(cid))).all()
    assert len(rows) == 1
    assert c.get(f"/api/v1/projects/{pid}/conversations/{cid}").json()["message_count"] == 1


def test_concurrent_sends_get_unique_gap_free_seq(user_client):
    c, _, uid = user_client()
    pid = project(c)["id"]
    cid = thread(c, pid)["id"]
    from app.db.models import Project, User

    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            with get_sessionmaker()() as s:
                user = s.get(User, uuid.UUID(uid))
                proj = s.get(Project, uuid.UUID(pid))
                conversation_service.post_user_message(
                    s, user, proj, uuid.UUID(cid), content=f"c{i}", client_message_id=None
                )
        except Exception as e:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    items = c.get(f"/api/v1/projects/{pid}/conversations/{cid}/messages?limit=200").json()["items"]
    assert [m["seq"] for m in items] == list(range(1, 13))
    assert sorted(m["content"] for m in items) == sorted(f"c{i}" for i in range(12))


def test_validation_errors_use_the_standard_envelope(user_client):
    c, _, _ = user_client()
    pid = project(c)["id"]
    cid = thread(c, pid)["id"]
    r = send(c, pid, cid, "   ")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = send(c, pid, cid, "x" * 20_001)
    assert r.status_code == 422
    r = c.post(f"/api/v1/projects/{pid}/conversations", json={"title": "   "})
    assert r.status_code == 422


def test_threads_list_newest_activity_first(user_client):
    c, _, _ = user_client()
    pid = project(c)["id"]
    a = thread(c, pid, "A")
    b = thread(c, pid, "B")
    send(c, pid, a["id"], "bump A")
    items = c.get(f"/api/v1/projects/{pid}/conversations").json()["items"]
    assert [t["title"] for t in items] == ["A", "B"] and b["id"] in [t["id"] for t in items]


def test_rename_uses_optimistic_concurrency(user_client):
    c, _, _ = user_client()
    pid = project(c)["id"]
    t = thread(c, pid)
    url = f"/api/v1/projects/{pid}/conversations/{t['id']}"
    ok = c.patch(url, json={"expected_version": 1, "title": "Eligibility"})
    assert ok.status_code == 200 and ok.json()["title"] == "Eligibility" and ok.json()["row_version"] == 2
    stale = c.patch(url, json={"expected_version": 1, "title": "Other"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    assert send(c, pid, t["id"], "hello").json()["conversation"]["title"] == "Eligibility"


def test_archived_project_is_read_only_for_chat(user_client):
    c, _, _ = user_client()
    p = project(c)
    cid = thread(c, p["id"])["id"]
    send(c, p["id"], cid, "before archive")
    assert c.patch(f"/api/v1/projects/{p['id']}", json={"expected_version": 1, "archived": True}).status_code == 200
    r = send(c, p["id"], cid, "after archive")
    assert r.status_code == 409 and r.json()["error"]["code"] == "project_archived"
    assert c.post(f"/api/v1/projects/{p['id']}/conversations", json={}).status_code == 409
    msgs = c.get(f"/api/v1/projects/{p['id']}/conversations/{cid}/messages").json()["items"]
    assert [m["content"] for m in msgs] == ["before archive"]  # history stays readable


def test_delete_thread_removes_messages_and_audits_without_text(user_client, db):
    c, _, _ = user_client()
    pid = project(c)["id"]
    t = thread(c, pid, "Secret plan")
    send(c, pid, t["id"], "confidential content")
    assert c.delete(f"/api/v1/projects/{pid}/conversations/{t['id']}").status_code == 204
    assert c.get(f"/api/v1/projects/{pid}/conversations/{t['id']}").status_code == 404
    assert db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(t["id"]))).all() == []
    events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(t["id"]))).all()
    assert {e.action for e in events} == {"conversation.created", "conversation.deleted"}
    dumped = repr([(e.before, e.after, e.reason) for e in events])
    assert "Secret plan" not in dumped and "confidential" not in dumped


def test_deleting_project_cascades_threads(user_client, db):
    c, _, _ = user_client()
    pid = project(c)["id"]
    cid = thread(c, pid)["id"]
    send(c, pid, cid, "goes away with the project")
    assert c.delete(f"/api/v1/projects/{pid}").status_code == 204
    assert db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(cid))).all() == []


def test_history_survives_a_fresh_application_instance_and_new_login(user_client, app):
    """Persistence is in PostgreSQL, not in the process: a brand-new app instance and session see it."""
    c, email, _ = user_client()
    pid = project(c, "Persisted")["id"]
    cid = thread(c, pid)["id"]
    send(c, pid, cid, "remember me")
    assert c.post("/api/v1/auth/logout").status_code == 204

    fresh = create_app()
    fresh.dependency_overrides = dict(app.dependency_overrides)
    with TestClient(fresh, headers=CSRF, base_url="http://localhost:8000") as c2:
        assert c2.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
        threads = c2.get(f"/api/v1/projects/{pid}/conversations").json()["items"]
        assert [t["id"] for t in threads] == [cid]
        msgs = c2.get(f"/api/v1/projects/{pid}/conversations/{cid}/messages").json()["items"]
        assert [m["content"] for m in msgs] == ["remember me"]


def test_jobs_list_is_owner_and_project_scoped(user_client, dispatcher):
    a, _, _ = user_client()
    b, _, _ = user_client()
    pid = project(a)["id"]
    jid = a.post("/api/v1/jobs/ping", json={"project_id": pid, "delay_s": 31}).json()["id"]
    a.post("/api/v1/jobs/ping", json={"delay_s": 32})  # not attached to the project
    items = a.get(f"/api/v1/jobs?project_id={pid}&active=true").json()["items"]
    assert [j["id"] for j in items] == [jid] and items[0]["project_id"] == pid
    assert b.get(f"/api/v1/jobs?project_id={pid}").status_code == 404
    assert b.get("/api/v1/jobs").json()["items"] == []
    a.post(f"/api/v1/jobs/{jid}/cancel")
    assert a.get(f"/api/v1/jobs?project_id={pid}&active=true").json()["items"] == []
