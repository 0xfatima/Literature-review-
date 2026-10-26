"""HTTP hardening applied by the API middleware (Phase 14). Stdlib only, so it is unit-testable anywhere.

* Security headers on every API response (a handler may set its own stricter value first; we never override it).
* A request-body ceiling for non-upload routes, checked from Content-Length before the body is read. The edge proxy
  enforces the same limits (infra/proxy/nginx.conf); this is defence in depth when the API is reached directly.
* The key for the per-session write limiter: a hash of the session cookie, never the cookie itself.
"""

from __future__ import annotations

import re

from app.core.security import SAFE_METHODS, hash_token

UPLOAD_PATH = re.compile(r"^/api/v1/projects/[0-9a-fA-F-]{36}/documents/?$")
UPLOAD_ENVELOPE_BYTES = 1024 * 1024  # multipart framing on top of the file itself


def security_headers(*, path: str, hsts_max_age_s: int | None) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    }
    if path.startswith("/api/"):
        # JSON/attachments only: nothing here is meant to render, run script or be framed; never cache private data.
        headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        headers["Cache-Control"] = "no-store"
    if hsts_max_age_s:
        headers["Strict-Transport-Security"] = f"max-age={int(hsts_max_age_s)}; includeSubDomains"
    return headers


def body_limit(path: str, *, max_body: int, upload_max: int) -> int:
    return upload_max + UPLOAD_ENVELOPE_BYTES if UPLOAD_PATH.match(path) else max_body


def body_too_large(method: str, path: str, content_length: str | None, *, max_body: int, upload_max: int) -> bool:
    """True when the declared body exceeds the route's limit. A malformed length is treated as too large."""
    if method.upper() in SAFE_METHODS or content_length is None:
        return False
    try:
        declared = int(content_length)
    except ValueError:
        return True
    return declared < 0 or declared > body_limit(path, max_body=max_body, upload_max=upload_max)


def write_limit_key(method: str, path: str, session_cookie: str | None) -> str | None:
    """Limiter key for a state-changing API request made with a session; None = not limited here (sign-in and
    registration have their own stricter per-IP/per-account limits)."""
    if method.upper() in SAFE_METHODS or not path.startswith("/api/") or not session_cookie:
        return None
    return f"writes:{hash_token(session_cookie)[:32]}"
