# stdlib-only
from app.core.http_security import body_limit, body_too_large, security_headers, write_limit_key
from app.core.settings_checks import find_unsafe_settings

UP = "/api/v1/projects/0b5c3f0e-1111-4222-8333-944445555666/documents"


def test_api_responses_get_strict_headers_and_no_store():
    h = security_headers(path="/api/v1/projects", hsts_max_age_s=None)
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert h["Cache-Control"] == "no-store" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in h


def test_non_api_paths_get_no_csp_and_hsts_only_when_enabled():
    h = security_headers(path="/docs", hsts_max_age_s=100)
    assert "Content-Security-Policy" not in h and h["Strict-Transport-Security"].startswith("max-age=100")


def test_body_limits_differ_for_uploads():
    assert body_limit("/api/v1/projects", max_body=10, upload_max=1000) == 10
    assert body_limit(UP, max_body=10, upload_max=1000) > 1000


def test_body_too_large_checks_declared_length_on_writes_only():
    kw = {"max_body": 100, "upload_max": 1000}
    assert body_too_large("POST", "/api/v1/projects", "101", **kw)
    assert not body_too_large("POST", "/api/v1/projects", "100", **kw)
    assert not body_too_large("GET", "/api/v1/projects", "999999", **kw)
    assert not body_too_large("POST", UP, "500", **kw)
    assert body_too_large("POST", "/api/v1/projects", "abc", **kw)
    assert body_too_large("POST", "/api/v1/projects", "-1", **kw)
    assert not body_too_large("POST", "/api/v1/projects", None, **kw)


def test_write_limit_key_is_hashed_and_only_for_session_writes():
    k = write_limit_key("POST", "/api/v1/projects", "secret-cookie-value")
    assert k and k.startswith("writes:") and "secret-cookie-value" not in k
    assert write_limit_key("GET", "/api/v1/projects", "c") is None
    assert write_limit_key("POST", "/api/v1/auth/login", None) is None
    assert write_limit_key("POST", "/healthz", "c") is None


def test_write_limit_cannot_be_disabled_in_production():
    base = {
        "app_env": "production",
        "session_secret": "x" * 40,
        "cookie_secure": True,
        "database_url": "postgresql://u:real@h/db",
    }
    assert any("RATE_LIMIT_WRITES_PER_MIN" in p for p in find_unsafe_settings({**base, "rate_limit_writes_per_min": 0}))
    assert not any("RATE_LIMIT_WRITES" in p for p in find_unsafe_settings({**base, "rate_limit_writes_per_min": 600}))
