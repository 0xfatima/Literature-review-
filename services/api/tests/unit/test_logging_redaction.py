# stdlib-only
import io
import json
import logging

from app.core.context import reset_correlation_id, set_correlation_id
from app.core.logging import REDACTED, JsonFormatter, redact, redact_text


def _emit(msg, **extra):
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    lg = logging.getLogger("t." + msg[:5])
    lg.handlers[:] = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    lg.info(msg, extra=extra)
    return json.loads(buf.getvalue())


def test_redacts_sensitive_keys_recursively():
    out = redact(
        {
            "user": "a",
            "password": "hunter2",
            "nested": {"api_key": "k", "ok": 1},
            "list": [{"Authorization": "Bearer abcdefgh12345"}],
        }
    )
    assert out["password"] == REDACTED and out["nested"]["api_key"] == REDACTED
    assert out["list"][0]["Authorization"] == REDACTED and out["nested"]["ok"] == 1 and out["user"] == "a"


def test_redacts_inline_secrets_in_text():
    assert "hunter2" not in redact_text("login failed password=hunter2 for x")
    assert "abcdefgh12345" not in redact_text("header Authorization: Bearer abcdefgh12345")
    assert "s3cr3t" not in redact_text("connect postgresql://srs:s3cr3t@db:5432/srs")
    assert "token=" in redact_text("token=abc123")  # key kept, value gone
    assert "abc123" not in redact_text("token=abc123")


def test_formatter_emits_json_with_correlation_and_redacts_extras():
    t = set_correlation_id("corr-12345678")
    try:
        rec = _emit("hello", session_token="zzz", count=3)
    finally:
        reset_correlation_id(t)
    assert rec["correlation_id"] == "corr-12345678" and rec["count"] == 3
    assert rec["session_token"] == REDACTED and rec["level"] == "INFO" and rec["msg"] == "hello"


def test_exception_text_is_redacted():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    lg = logging.getLogger("t.exc")
    lg.handlers[:] = [h]
    lg.propagate = False
    try:
        raise RuntimeError("dsn=postgresql://u:topsecret@h/db password=topsecret")
    except RuntimeError:
        lg.exception("boom")
    assert "topsecret" not in buf.getvalue()


def test_non_sensitive_values_untouched():
    assert redact({"count": 5, "path": "/api/v1/projects"}) == {"count": 5, "path": "/api/v1/projects"}
