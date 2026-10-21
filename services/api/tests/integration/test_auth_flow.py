import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.security import hash_token
from app.db.models import AuthSession, User
from tests.integration.conftest import CSRF, PASSWORD, new_email


def test_register_returns_no_secrets_and_hashes_with_argon2id(make_client, db):
    c, email = make_client(), new_email()
    r = c.post("/api/v1/auth/register", json={"email": email.upper(), "password": PASSWORD})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == email and "password" not in r.text.lower().replace("passwords", "")
    assert "password_hash" not in body
    stored = db.scalar(select(User).where(User.email == email))
    assert stored.password_hash.startswith("$argon2id$") and PASSWORD not in stored.password_hash


def test_duplicate_email_is_rejected_case_insensitively(make_client):
    c, email = make_client(), new_email()
    assert c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD}).status_code == 201
    r = c.post("/api/v1/auth/register", json={"email": email.upper(), "password": PASSWORD})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"


def test_weak_password_rejected_with_field_detail(make_client):
    r = make_client().post("/api/v1/auth/register", json={"email": new_email(), "password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_login_cookie_flags_and_session_stored_hashed(make_client, db):
    c, email = make_client(), new_email()
    c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    r = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=lax" in sc and "Path=/" in sc
    token = c.cookies.get("srs_session")
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    assert row is not None and row.token_hash != token  # raw token never stored


def test_wrong_password_and_unknown_email_are_indistinguishable(make_client):
    c, email = make_client(), new_email()
    c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    a = c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-123"})
    b = c.post("/api/v1/auth/login", json={"email": new_email(), "password": "wrong-password-123"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_me_requires_valid_session(user_client, make_client):
    c, email, uid = user_client()
    assert c.get("/api/v1/auth/me").json()["id"] == uid
    anon = make_client()
    assert anon.get("/api/v1/auth/me").status_code == 401
    anon.cookies.set("srs_session", "garbage-token-value")
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_logout_revokes_the_session_server_side(user_client, make_client):
    c, _, _ = user_client()
    token = c.cookies.get("srs_session")
    out = c.post("/api/v1/auth/logout")
    assert out.status_code == 204 and out.content == b""  # a 204 must have no body
    cookie_header = out.headers["set-cookie"]
    assert "srs_session=" in cookie_header and ("Max-Age=0" in cookie_header or "expires=" in cookie_header.lower())
    replay = make_client()
    replay.cookies.set("srs_session", token)  # attacker replays the old cookie
    assert replay.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_rejected(user_client, db):
    c, _, uid = user_client()
    db.execute(update(AuthSession).where(AuthSession.user_id == uuid.UUID(uid))
               .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    assert c.get("/api/v1/auth/me").status_code == 401


def test_disabled_user_cannot_log_in_or_use_existing_session(user_client, make_client, db):
    c, email, uid = user_client()
    db.execute(update(User).where(User.id == uuid.UUID(uid)).values(status="disabled"))
    db.commit()
    assert c.get("/api/v1/auth/me").status_code == 401
    assert make_client().post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401


def test_validation_errors_never_echo_submitted_secrets(make_client):
    secret = "my-super-secret-value-xyz"
    r = make_client().post("/api/v1/auth/login", json={"password": secret})  # email missing
    assert r.status_code == 422 and secret not in r.text


def test_csrf_header_and_origin_enforced(app):
    from fastapi.testclient import TestClient
    bare = TestClient(app, base_url="http://localhost:8000")  # no CSRF header
    r = bare.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x" * 12})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_rejected"
    evil = TestClient(app, headers={"X-SRS-CSRF": "1", "Origin": "https://evil.example"})
    assert evil.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x" * 12}).status_code == 403
    assert bare.get("/healthz").status_code == 200  # safe methods unaffected


def test_login_is_rate_limited_with_retry_after(make_client):
    c, email = make_client(), new_email()
    codes = [c.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-123"})
             for _ in range(12)]
    assert codes[-1].status_code == 429 and int(codes[-1].headers["retry-after"]) >= 1
    assert codes[-1].json()["error"]["code"] == "rate_limited"


def test_error_envelope_shape_and_correlation_id(make_client):
    r = make_client().get("/api/v1/auth/me", headers={"X-Correlation-ID": "test-corr-12345"})
    err = r.json()["error"]
    assert set(err) == {"code", "message", "details", "correlation_id"}
    assert err["correlation_id"] == "test-corr-12345" and r.headers["x-correlation-id"] == "test-corr-12345"
