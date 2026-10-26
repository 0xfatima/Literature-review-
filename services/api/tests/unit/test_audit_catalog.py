# stdlib-only
import pathlib
import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from app.audit.catalog import CATEGORY_NAMES, category_of, label_of, prefixes_for, required_actions
from app.audit.query import (
    actor_text,
    clamp_limit,
    csv_header,
    csv_row,
    csv_safe,
    decode_cursor,
    encode_cursor,
    event_view,
)
from app.core.errors import ValidationFailed
from tests.helpers import raises

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _emitted_actions() -> set[str]:
    found: set[str] = set()
    for f in APP.rglob("*.py"):
        found.update(re.findall(r'action\s*=\s*"([a-z_]+\.[a-z_]+)"', f.read_text()))
    return found


def test_every_required_significant_event_is_still_emitted_somewhere():
    missing = sorted(required_actions() - _emitted_actions())
    assert not missing, f"required audit actions no longer emitted: {missing}"


def test_every_emitted_action_has_a_named_category():
    uncategorised = sorted(a for a in _emitted_actions() if category_of(a) == "other")
    assert not uncategorised, uncategorised


def test_categories_and_labels():
    assert category_of("screening.decided") == "screening" and category_of("x.y") == "other"
    assert "screening." in prefixes_for("screening") and "other" in CATEGORY_NAMES
    assert label_of("protocol.approved") == "Protocol approved"
    assert label_of("document.retry") == "Document retry"


def test_cursor_roundtrip_and_rejection():
    at, eid = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC), uuid.uuid4()
    assert decode_cursor(encode_cursor(at, eid)) == (at, eid)
    assert decode_cursor(None) is None
    for bad in ("not-base64!", encode_cursor(at, eid)[:-6], "MjAyNnx4"):
        with raises(ValidationFailed):
            decode_cursor(bad)


def test_limit_is_clamped():
    assert clamp_limit(0) == 50 and clamp_limit(10_000) == 200 and clamp_limit(5) == 5


def test_event_view_distinguishes_you_another_user_and_system():
    me, other = uuid.uuid4(), uuid.uuid4()
    base = dict(
        id=uuid.uuid4(),
        at=datetime(2026, 1, 1, tzinfo=UTC),
        action="document.purged",
        entity_type="document",
        entity_id=None,
        reason=None,
        before=None,
        after=None,
        correlation_id="c",
    )
    sys_v = event_view(SimpleNamespace(**base, actor_id=None, actor_process="worker:system.purge_documents"), me)
    assert sys_v["actor"] == {"kind": "system", "is_you": False, "process": "worker:system.purge_documents"}
    assert actor_text(sys_v["actor"]) == "system (worker:system.purge_documents)"
    assert actor_text(event_view(SimpleNamespace(**base, actor_id=me, actor_process=None), me)["actor"]) == "you"
    assert actor_text(event_view(SimpleNamespace(**base, actor_id=other, actor_process=None), me)["actor"]) == (
        "another user"
    )
    assert sys_v["category"] == "documents" and sys_v["label"] == "File purged (retention)"


def test_csv_neutralises_formulas():
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)" and csv_safe(None) == "" and csv_safe("ok") == "ok"
    v = {
        "at": "t",
        "action": "a.b",
        "category": "other",
        "label": "A b",
        "entity_type": "x",
        "entity_id": None,
        "actor": {"kind": "system", "is_you": False, "process": None},
        "reason": "@cmd",
        "correlation_id": None,
    }
    assert "'@cmd" in csv_row(v) and csv_header().startswith("at,action,")
