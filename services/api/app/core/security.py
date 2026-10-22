"""Security primitives that need no third-party packages (so they are unit-testable anywhere).

Password hashing (Argon2id) lives in app.core.passwords because it needs argon2-cffi.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Iterable
from urllib.parse import urlsplit

from app.core.errors import ValidationFailed

SESSION_TOKEN_BYTES = 32
PASSWORD_MIN_LEN = 12
PASSWORD_MAX_LEN = 128
EMAIL_MAX_LEN = 254
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Deliberately tiny: a full breached-password check is a later hardening item (documented).
_COMMON = {"password1234", "123456789012", "qwertyuiop12", "letmein123456", "administrator1"}


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 hex. Tokens are high-entropy random values, so a fast hash is appropriate (no salt needed)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > EMAIL_MAX_LEN or not _EMAIL_RE.match(email):
        raise ValidationFailed("Enter a valid email address.", details=[{"field": "email", "issue": "invalid"}])
    return email


def email_fingerprint(email: str) -> str:
    """Stable, non-reversible handle for audit/rate-limit keys (keeps raw emails out of logs)."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:24]


def validate_password(password: str, email: str | None = None) -> None:
    issues: list[str] = []
    if len(password) < PASSWORD_MIN_LEN:
        issues.append(f"at least {PASSWORD_MIN_LEN} characters")
    if len(password.encode("utf-8")) > PASSWORD_MAX_LEN * 4 or len(password) > PASSWORD_MAX_LEN:
        issues.append(f"at most {PASSWORD_MAX_LEN} characters")
    if email and password.strip().lower() == email.strip().lower():
        issues.append("must not equal your email")
    if password.lower() in _COMMON or len(set(password)) < 4:
        issues.append("too common or repetitive")
    if issues:
        raise ValidationFailed(
            "Password does not meet requirements: " + "; ".join(issues) + ".",
            details=[{"field": "password", "issue": i} for i in issues],
        )


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-srs-csrf"


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def csrf_violation(method: str, headers: dict[str, str], allowed_origins: Iterable[str]) -> str | None:
    """Return a reason string if a state-changing request fails CSRF checks, else None.

    Defence in depth (cookie is also SameSite=Lax):
      1. A custom header must be present (cannot be sent cross-site without a CORS preflight we do not allow).
      2. If Origin is present it must be allow-listed; else if Referer is present its origin must be.
    `headers` keys must be lower-case.
    """
    if method.upper() in SAFE_METHODS:
        return None
    if headers.get(CSRF_HEADER) != "1":
        return "missing_csrf_header"
    allowed = {o.rstrip("/").lower() for o in allowed_origins if o}
    origin = headers.get("origin")
    if origin is not None:
        return None if origin.rstrip("/").lower() in allowed else "origin_not_allowed"
    referer = headers.get("referer")
    if referer:
        ro = _origin_of(referer)
        return None if ro in allowed else "referer_not_allowed"
    return None
