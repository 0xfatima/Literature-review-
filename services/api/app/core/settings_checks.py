"""Pure validation of configuration safety. Stdlib only so it is testable without pydantic."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PLACEHOLDER_MARKERS = ("CHANGE_ME", "changeme", "placeholder")
LOCAL_ENVS = frozenset({"development", "test"})


def find_unsafe_settings(values: Mapping[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means OK. Only enforced outside local envs."""
    env = str(values.get("app_env", "development")).lower()
    if env in LOCAL_ENVS:
        return []
    problems: list[str] = []
    secret = str(values.get("session_secret") or "")  # None / unset counts as missing
    if not secret or any(m.lower() in secret.lower() for m in PLACEHOLDER_MARKERS) or len(secret) < 32:
        problems.append("SESSION_SECRET must be a real random value of at least 32 characters")
    if not values.get("cookie_secure"):
        problems.append("COOKIE_SECURE must be true outside development/test")
    db = str(values.get("database_url", ""))
    if any(m.lower() in db.lower() for m in PLACEHOLDER_MARKERS):
        problems.append("DATABASE_URL still contains a placeholder password")
    origins = str(values.get("cors_allowed_origins", ""))
    if "*" in origins:
        problems.append("CORS_ALLOWED_ORIGINS must not contain a wildcard")
    if float(values.get("workflow_debug_node_delay_s") or 0) != 0:
        problems.append("WORKFLOW_DEBUG_NODE_DELAY_S is a test hook and must be 0 outside development/test")
    if env not in {"staging", "production"}:
        problems.append(f"APP_ENV '{env}' is not one of development|test|staging|production")
    return problems
