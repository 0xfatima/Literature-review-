# stdlib-only
from app.core.errors import ValidationFailed
from app.core.security import (
    csrf_violation,
    email_fingerprint,
    hash_token,
    new_session_token,
    normalize_email,
    validate_password,
)
from tests.helpers import raises

ORIGINS = ["http://localhost:3000"]
H = {"x-srs-csrf": "1"}


def test_session_tokens_are_unique_and_high_entropy():
    toks = {new_session_token() for _ in range(200)}
    assert len(toks) == 200 and all(len(t) >= 43 for t in toks)


def test_token_hash_is_deterministic_hex_and_not_the_token():
    t = new_session_token()
    assert hash_token(t) == hash_token(t) and len(hash_token(t)) == 64 and t not in hash_token(t)
    assert hash_token("a") != hash_token("b")


def test_email_normalisation_and_validation():
    assert normalize_email("  Alice@Example.COM ") == "alice@example.com"
    for bad in ("", "no-at", "a@b", "a b@c.d", "x" * 260 + "@a.bc"):
        with raises(ValidationFailed):
            normalize_email(bad)
    assert email_fingerprint("A@x.io") == email_fingerprint(" a@X.io") and "@" not in email_fingerprint("a@x.io")


def test_password_policy():
    validate_password("a-reasonably-long-pass")
    for bad in ("short", "x" * 129, "aaaaaaaaaaaaaaaa", "password1234"):
        with raises(ValidationFailed):
            validate_password(bad)
    with raises(ValidationFailed, "must not equal your email"):
        validate_password("someone@example.com", "someone@example.com")


def test_csrf_safe_methods_exempt():
    assert csrf_violation("GET", {}, ORIGINS) is None
    assert csrf_violation("OPTIONS", {}, ORIGINS) is None


def test_csrf_requires_custom_header():
    assert csrf_violation("POST", {}, ORIGINS) == "missing_csrf_header"
    assert csrf_violation("DELETE", {"origin": "http://localhost:3000"}, ORIGINS) == "missing_csrf_header"


def test_csrf_origin_allowlist():
    assert csrf_violation("POST", {**H, "origin": "http://localhost:3000"}, ORIGINS) is None
    assert csrf_violation("POST", {**H, "origin": "https://evil.example"}, ORIGINS) == "origin_not_allowed"
    assert csrf_violation("POST", {**H, "origin": "null"}, ORIGINS) == "origin_not_allowed"


def test_csrf_referer_fallback_and_trailing_slash():
    assert csrf_violation("PATCH", {**H, "referer": "http://localhost:3000/projects/1"}, ORIGINS) is None
    assert csrf_violation("PATCH", {**H, "referer": "https://evil.example/x"}, ORIGINS) == "referer_not_allowed"
    assert csrf_violation("POST", {**H, "origin": "http://localhost:3000"}, ["http://localhost:3000/"]) is None


def test_csrf_non_browser_client_with_header_only_is_accepted():
    # curl/tests: no Origin/Referer. Cross-site browsers always send Origin on POST, so this stays safe.
    assert csrf_violation("POST", H, ORIGINS) is None
