"""Encryption at rest for small user secrets (their own AI provider API keys).

Fernet (AES-128-CBC + HMAC-SHA256, from the `cryptography` package). The key is USER_SECRETS_KEY when set (either a
Fernet key or any long random string, which is stretched with SHA-256), otherwise it is derived from SESSION_SECRET.
Changing the key makes stored user keys unreadable: users are then asked to enter their key again (nothing crashes).
Plain text never reaches logs, audit events or API responses; only a short hint (last four characters) is shown.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_DEV_FALLBACK = "srs-development-only-user-secrets-key"  # noqa: S105  (development/test only; see _material)


class SecretUnavailable(Exception):
    """The stored secret cannot be decrypted (the key changed) or no key is configured."""


def _material(settings: Any) -> str:
    raw = str(getattr(settings, "user_secrets_key", None) or getattr(settings, "session_secret", None) or "")
    if raw:
        return raw
    if str(getattr(settings, "app_env", "development")).lower() in ("development", "test"):
        return _DEV_FALLBACK
    raise SecretUnavailable("Set USER_SECRETS_KEY (or SESSION_SECRET) to store user API keys.")


def _fernet(settings: Any) -> Fernet:
    raw = _material(settings)
    try:
        if len(raw) == 44:
            return Fernet(raw.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        pass
    digest = hashlib.sha256(b"srs-user-secrets:v1:" + raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(settings: Any, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(settings: Any, token: str) -> str:
    try:
        return _fernet(settings).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise SecretUnavailable("The stored key cannot be read with the current server key.") from e


def hint(secret: str) -> str:
    """'…AbCd' for display. Short secrets get no hint at all."""
    s = secret.strip()
    return f"…{s[-4:]}" if len(s) >= 12 else "…"
