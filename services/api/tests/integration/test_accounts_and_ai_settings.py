"""Password reset, e-mail verification, personal AI settings and the AI-built search expression (real Postgres)."""

import re

import pytest

from app.providers.email import Mail
from app.providers.llm import LLMGateway, LLMResult
from tests.integration.conftest import PASSWORD, new_email
from tests.integration.support import approved

NEW_PASSWORD = "another-long-password-2"


class Outbox:
    def __init__(self):
        self.sent: list[Mail] = []
        self.name = "test"

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return True

    def token(self, kind: str) -> str:
        mail = [m for m in self.sent if kind in m.text][-1]
        return re.search(r"token=([\w\-]+)", mail.text).group(1)


@pytest.fixture
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr("app.api.v1.auth.build_mailer", lambda _s: box)
    return box


def test_password_reset_by_email_link(make_client, user_client, outbox):
    c, email, _ = user_client()
    other = make_client()
    assert other.post("/api/v1/auth/password-reset/request", json={"email": email}).status_code == 202
    # unknown addresses get the same answer and no mail (no account enumeration)
    before = len(outbox.sent)
    assert other.post("/api/v1/auth/password-reset/request", json={"email": new_email()}).status_code == 202
    assert len(outbox.sent) == before
    token = outbox.token("reset-password")
    weak = other.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": "short"})
    assert weak.status_code == 422  # the token survives a rejected password
    ok = other.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": NEW_PASSWORD})
    assert ok.status_code == 204
    again = other.post("/api/v1/auth/password-reset/confirm", json={"token": token, "password": NEW_PASSWORD})
    assert again.status_code == 422  # one use only
    assert c.get("/api/v1/auth/me").status_code == 401  # every session was signed out
    assert other.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401
    assert other.post("/api/v1/auth/login", json={"email": email, "password": NEW_PASSWORD}).status_code == 200


def test_only_the_newest_reset_link_works(make_client, user_client, outbox):
    _, email, _ = user_client()
    c = make_client()
    c.post("/api/v1/auth/password-reset/request", json={"email": email})
    first = outbox.token("reset-password")
    c.post("/api/v1/auth/password-reset/request", json={"email": email})
    assert (
        c.post("/api/v1/auth/password-reset/confirm", json={"token": first, "password": NEW_PASSWORD}).status_code
        == 422
    )


def test_email_verification_can_be_required(make_client, set_settings, outbox):
    set_settings(auth_require_email_verification=True)
    c = make_client()
    email = new_email()
    r = c.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code == 201 and r.json()["verification_required"] and r.json()["verification_sent"]
    assert r.json()["email_verified"] is False
    denied = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "email_not_verified"
    assert c.post("/api/v1/auth/verify-email/request", json={"email": email}).status_code == 202
    token = outbox.token("verify-email")
    assert c.post("/api/v1/auth/verify-email/confirm", json={"token": token}).json()["email_verified"] is True
    assert c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert c.get("/api/v1/auth/config").json()["email_verification_required"] is True


def test_registration_refuses_impossible_addresses(make_client):
    c = make_client()
    for bad in ("abc@gmail.com", "someone@gmial.com", "x@mailinator.com"):
        r = c.post("/api/v1/auth/register", json={"email": bad, "password": PASSWORD})
        assert r.status_code == 422, bad


def test_personal_ai_settings_store_the_key_encrypted_and_never_return_it(user_client, db):
    c, _, uid = user_client()
    view = c.get("/api/v1/auth/me/llm").json()
    assert view["saved"] is None and view["default"]["provider"] == "gemini"
    assert any(m["pricing"] == "free" for p in view["providers"] for m in p["models"])
    r = c.put(
        "/api/v1/auth/me/llm",
        json={"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key": "gsk_" + "x" * 30},
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "gsk_" not in body and r.json()["saved"]["has_key"] and r.json()["saved"]["key_hint"] == "…xxxx"
    assert r.json()["effective"]["provider"] == "groq" and r.json()["effective"]["key_source"] == "user"
    from sqlalchemy import text

    stored = db.execute(
        text("SELECT api_key_ciphertext FROM user_llm_settings WHERE user_id = :u"), {"u": uid}
    ).scalar()
    assert stored and "gsk_" not in stored
    audit = c.get("/api/v1/auth/me/audit-events").text
    assert "gsk_" not in audit
    # switching provider without a new key drops the old key (it must never be sent to another provider)
    r = c.put("/api/v1/auth/me/llm", json={"provider": "gemini", "model": "gemini-flash-latest"})
    assert r.json()["saved"]["has_key"] is False
    assert c.put("/api/v1/auth/me/llm", json={"provider": "evil", "model": "m"}).status_code == 422
    assert c.delete("/api/v1/auth/me/llm").json()["saved"] is None


def test_jobs_and_requests_use_the_owners_own_key(user_client, db):
    from app.core.config import get_settings
    from app.services import user_llm_service

    c, _, uid = user_client()
    c.put("/api/v1/auth/me/llm", json={"provider": "openrouter", "model": "a/b:free", "api_key": "sk-or-" + "y" * 30})
    import uuid as _uuid

    s = user_llm_service.settings_for(db, get_settings(), _uuid.UUID(uid))
    assert (s.llm_provider, s.llm_model, s.llm_key_source) == ("openrouter", "a/b:free", "user")
    assert s.llm_api_key == "sk-or-" + "y" * 30
    assert user_llm_service.settings_for(db, get_settings(), _uuid.uuid4()) is get_settings()


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, expression):
        self.expression, self.calls = expression, 0

    def generate_json(self, **kw):
        self.calls += 1
        return LLMResult(provider=self.name, model=self.model, data={"concepts": [], "expression": self.expression})


def test_the_search_plan_uses_an_ai_built_expression_and_caches_it(user_client, dispatcher, monkeypatch):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    fake = FakeProvider('(exercise OR "physical activity") AND (depression OR "depressive symptoms")')
    monkeypatch.setattr("app.services.search_suggestion_service.build_gateway", lambda _s: LLMGateway(fake))
    plan = c.post(f"/api/v1/projects/{pid}/search/plan", json={"tier": "open_access"}).json()
    if plan["expression_source"] == "search_strategy":
        pytest.skip("the fixture protocol has a Boolean strategy, which always wins")
    assert plan["expression_source"] == "ai" and "physical activity" in plan["expression"]
    c.post(f"/api/v1/projects/{pid}/search/plan", json={"tier": "open_access"})
    assert fake.calls == 1  # cached per approved protocol version
    fake.expression = "exercise AND mood"
    again = c.post(f"/api/v1/projects/{pid}/search/suggest-expression", json={"tier": "open_access"}).json()
    assert again["expression"] == "exercise AND mood" and fake.calls == 2


def test_chat_commands_return_the_view_and_the_request_to_confirm(user_client, dispatcher):
    c, _, _ = user_client()
    pid = approved(c, dispatcher)
    cid = c.post(f"/api/v1/projects/{pid}/conversations", json={"title": "Commands"}).json()["id"]
    r = c.post(
        f"/api/v1/projects/{pid}/assist/command",
        json={"text": "run the search", "current_view": None, "conversation_id": cid, "record": True},
    ).json()
    assert r["action"] == "start_search" and r["confirm"] and r["view"] == "search"
    assert r["request"]["path"] == f"/projects/{pid}/search-runs" and r["request"]["body"]["tier"] == "open_access"
    msgs = c.get(f"/api/v1/projects/{pid}/conversations/{cid}/messages").json()["items"]
    assert [m["role"] for m in msgs[-2:]] == ["user", "assistant"] and msgs[-1]["payload"]["type"] == "command"
    nav = c.post(f"/api/v1/projects/{pid}/assist/command", json={"text": "next step", "current_view": "search"}).json()
    assert nav["action"] == "next_step" and nav["view"] == "screening" and nav["request"] is None
    none = c.post(f"/api/v1/projects/{pid}/assist/command", json={"text": "hello there, how are you?"}).json()
    assert none == {"action": "none"}
    # the confirmed request is the ordinary endpoint
    assert (
        c.request(r["request"]["method"], "/api" + "/v1" + r["request"]["path"], json=r["request"]["body"]).status_code
        == 202
    )
