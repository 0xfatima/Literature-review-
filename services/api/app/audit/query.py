"""Audit-log presentation helpers: keyset cursors, event views, CSV rows (Phase 14). Stdlib only.

Views never add free text: events store field NAMES and ids, not user content (see services/audit_service.py), and
before/after payloads were already redacted when written.
"""

from __future__ import annotations

import base64
import csv
import io
import uuid
from datetime import datetime
from typing import Any

from app.audit.catalog import category_of, label_of
from app.core.errors import ValidationFailed

MAX_LIMIT = 200
CSV_COLUMNS = (
    "at",
    "action",
    "category",
    "label",
    "entity_type",
    "entity_id",
    "actor",
    "actor_process",
    "reason",
    "correlation_id",
)


def encode_cursor(at: datetime, event_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{at.isoformat()}|{event_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at_s, id_s = raw.split("|", 1)
        at = datetime.fromisoformat(at_s)
        if at.tzinfo is None:
            raise ValueError("naive timestamp")
        return at, uuid.UUID(id_s)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValidationFailed("Invalid cursor.", details=[{"field": "cursor", "issue": "invalid"}]) from e


def clamp_limit(limit: int | None, default: int = 50) -> int:
    return max(1, min(MAX_LIMIT, int(limit or default)))


def actor_view(actor_id: uuid.UUID | None, actor_process: str | None, viewer_id: uuid.UUID) -> dict[str, Any]:
    if actor_id is None:
        return {"kind": "system", "is_you": False, "process": actor_process}
    return {"kind": "user", "is_you": actor_id == viewer_id, "process": actor_process}


def actor_text(actor: dict[str, Any]) -> str:
    if actor["kind"] == "system":
        return f"system ({actor['process']})" if actor.get("process") else "system"
    return "you" if actor["is_you"] else "another user"


def event_view(e: Any, viewer_id: uuid.UUID) -> dict[str, Any]:
    """`e` is an AuditEvent-like object."""
    return {
        "id": str(e.id),
        "at": e.at.isoformat(),
        "action": e.action,
        "category": category_of(e.action),
        "label": label_of(e.action),
        "entity_type": e.entity_type,
        "entity_id": str(e.entity_id) if e.entity_id else None,
        "actor": actor_view(e.actor_id, getattr(e, "actor_process", None), viewer_id),
        "reason": e.reason,
        "before": e.before,
        "after": e.after,
        "correlation_id": e.correlation_id,
    }


def csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formulas (CSV injection)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_header() -> str:
    buf = io.StringIO()
    csv.writer(buf).writerow(CSV_COLUMNS)
    return buf.getvalue()


def csv_row(view: dict[str, Any]) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerow(
        [
            csv_safe(view["at"]),
            csv_safe(view["action"]),
            csv_safe(view["category"]),
            csv_safe(view["label"]),
            csv_safe(view["entity_type"]),
            csv_safe(view["entity_id"]),
            csv_safe(actor_text(view["actor"])),
            csv_safe(view["actor"].get("process")),
            csv_safe(view["reason"]),
            csv_safe(view["correlation_id"]),
        ]
    )
    return buf.getvalue()
