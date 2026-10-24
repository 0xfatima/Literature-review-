"""Text hygiene and normalisation for extracted document text. Stdlib only."""

from __future__ import annotations

import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
GARBLED_THRESHOLD = 0.3


def clean_page_text(raw: str) -> str:
    """Make extracted text safe to store: PostgreSQL text cannot hold NUL, and lone surrogates cannot be encoded.
    Control characters other than newline and tab are removed; the wording is otherwise left exactly as extracted,
    because later stages verify quotes against this stored text."""
    text = raw.encode("utf-8", "replace").decode("utf-8", "replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text)


def garbled_ratio(text: str) -> float:
    """Share of characters that signal a broken text layer: replacement characters, private-use code points and
    `(cid:N)` placeholders. A page of mostly such characters has no usable text."""
    stripped = re.sub(r"\s+", "", text)
    if not stripped:
        return 0.0
    cid = sum(len(m.group(0)) for m in re.finditer(r"\(cid:\d+\)", stripped))
    bad = sum(1 for ch in stripped if ch == "\ufffd" or unicodedata.category(ch) in ("Co", "Cn"))
    return min(1.0, (bad + cid) / len(stripped))


def match_tokens(text: str) -> list[str]:
    """Lower-case ASCII word tokens with accents folded, for matching titles and names only (never stored)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return [t for t in _NON_ALNUM.split(folded) if t]


def match_string(text: str) -> str:
    return " ".join(match_tokens(text))
