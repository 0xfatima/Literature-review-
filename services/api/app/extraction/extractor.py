"""AI evidence extraction: chunking, prompt, response schema, deterministic validation and merging. Stdlib only.

"AI proposes; deterministic code validates; humans decide." For each approved schema field the model answers
`found` (value + verbatim quote + page), `not_located` or `ambiguous`. Validation then:
  * accepts only field keys that are in the approved schema and pages that were in the request;
  * keeps a `found` answer only if its quote occurs in the cited page (whitespace/case/ligature-insensitive match,
    stored as the EXACT source substring) - or on another page of the same request, which is then cited instead;
  * coerces the value to the field type (number / boolean / choice / multi-choice / date / text); a value that does
    not fit is discarded, never "fixed";
  * checks every number in the value against the numbers in the quote (a numeric field whose number is not in the
    quote is discarded; for text fields a warning asks for review) and flags units (%) missing from the quote.
A discarded proposal leaves the field `pending` with a validation warning: nothing is ever filled in by guessing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

PROMPT_VERSION = "extraction-v1"
ANSWER_STATUSES = ("found", "not_located", "ambiguous")
MAX_VALUE_CHARS = 2000
MAX_NOTE_CHARS = 500
MAX_QUOTE_CHARS = 1200

SYSTEM = (
    "You extract data for a systematic review from the text of ONE research paper. Use ONLY the page text given. "
    "For each requested field answer 'found' only if the text states it; then give the value and copy a short quote "
    "word for word from the page that states it, with that page number. If the pages given do not state it, answer "
    "'not_located' (this is not evidence that it does not exist). If the text is contradictory or unclear, answer "
    "'ambiguous' and explain briefly in the note. Never infer, estimate, compute or use outside knowledge. A human "
    "will verify every answer."
)

SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "fields": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "field_key": {"type": "STRING", "description": "Exactly as given"},
                    "status": {"type": "STRING", "enum": list(ANSWER_STATUSES)},
                    "value": {"type": "STRING", "description": "The value as stated; for multiple choices join by ;"},
                    "page": {"type": "INTEGER", "description": "Page number of the quote"},
                    "quote": {"type": "STRING", "description": "Verbatim text from that page, at most 60 words"},
                    "note": {"type": "STRING", "description": "Short; required when ambiguous"},
                },
                "required": ["field_key", "status"],
            },
        }
    },
    "required": ["fields"],
}


@dataclass(frozen=True)
class PageText:
    page_id: str
    page_no: int
    text: str


@dataclass
class Outcome:
    """Validated answer for one field from one request (or the merged result for one paper)."""

    status: str  # found | not_located | ambiguous | rejected
    value: Any = None
    excerpt: str | None = None
    page_id: str | None = None
    page_no: int | None = None
    note: str | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    chunk_index: int = 0


# ---------- chunking and prompt ----------


def chunk_pages(pages: list[PageText], max_chars: int) -> list[list[PageText]]:
    """Group consecutive pages into requests of at most `max_chars` characters. A page is never split, so every quote
    keeps its page number; a single page longer than the limit forms its own request."""
    chunks: list[list[PageText]] = []
    cur: list[PageText] = []
    size = 0
    for p in pages:
        if not p.text.strip():
            continue
        n = len(p.text)
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def _field_line(fd: dict[str, Any]) -> str:
    parts = [f"- {fd['field_key']}: {fd.get('label') or fd['field_key']} (type: {fd.get('type') or 'text'})"]
    if fd.get("options"):
        parts.append(f"  options: {'; '.join(str(o) for o in fd['options'])}")
    if fd.get("hint"):
        parts.append(f"  guidance: {str(fd['hint'])[:300]}")
    return "\n".join(parts)


def build_prompt(fields: list[dict[str, Any]], title: str | None, chunk: list[PageText], part: int, parts: int) -> str:
    lines = [
        f"Paper title (from bibliographic metadata): {title or 'unknown'}",
        f"Text part {part} of {parts}. Pages given: {', '.join(str(p.page_no) for p in chunk)}.",
        "",
        "Fields to extract:",
        *(_field_line(fd) for fd in fields),
        "",
        "Answer every field once. Page text follows.",
    ]
    for p in chunk:
        lines += ["", f"=== PAGE {p.page_no} ===", p.text]
    return "\n".join(lines)


# ---------- quote location and numbers ----------


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Normalised text (NFKC, lower-case, quotes/dashes unified, whitespace collapsed) plus, for every normalised
    character, the index of the source character it came from."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).lower():
            if c in "\u2018\u2019\u201b`\u00b4":
                c = "'"
            elif c in "\u201c\u201d\u201e":
                c = '"'
            elif c in "\u2010\u2011\u2012\u2013\u2014\u2212":
                c = "-"
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def locate_quote(page_text: str, quote: str) -> str | None:
    """Exact substring of `page_text` that matches `quote` up to normalisation, or None. A quote is accepted only if it
    has at least 3 non-space characters (a bare number would match almost anywhere)."""
    q, _ = _norm_map(quote)
    if len(q.replace(" ", "")) < 3:
        return None
    t, idx = _norm_map(page_text)
    pos = t.find(q)
    if pos < 0:
        return None
    start, end = idx[pos], idx[pos + len(q) - 1] + 1
    return page_text[start:end]


_NUM = re.compile(r"(?<![\w.])[-\u2212]?\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[str]:
    """Numbers in text, normalised: thousands separators removed ("1,234" -> "1234"), unicode minus -> "-",
    trailing zeros of decimals dropped ("2.50" -> "2.5")."""
    found: set[str] = set()
    for m in _NUM.finditer(text):
        s = m.group(0).replace("\u2212", "-")
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        s = s.replace(",", ".")
        if "." in s and s.count(".") == 1:
            s = s.rstrip("0").rstrip(".")
        found.add(s)
    return found


def numeric_warnings(value: Any, excerpt: str | None) -> list[dict[str, Any]]:
    """Numbers / units in the value that the excerpt does not contain."""
    text = _value_text(value)
    if not text:
        return []
    warnings: list[dict[str, Any]] = []
    if not excerpt:
        if numbers_in(text):
            warnings.append({"code": "no_source_excerpt", "message": "Numeric value has no source excerpt."})
        return warnings
    missing = sorted(numbers_in(text) - numbers_in(excerpt))
    if missing:
        warnings.append(
            {
                "code": "number_not_in_excerpt",
                "message": f"Number(s) {', '.join(missing)} do not appear in the source excerpt.",
            }
        )
    if "%" in text and "%" not in excerpt and "percent" not in excerpt.lower():
        warnings.append({"code": "unit_not_in_excerpt", "message": "The value has a % unit the excerpt does not."})
    return warnings


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(str(v) for v in value.values())
    return str(value)


# ---------- value coercion ----------


def coerce_value(fd: dict[str, Any], raw: Any) -> tuple[Any, str | None]:
    """(value, problem). The value is converted to the field type; anything that does not fit is a problem."""
    ftype = str(fd.get("type") or "text")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, "empty value"
    if ftype == "number":
        if isinstance(raw, bool):
            return None, "not a number"
        if isinstance(raw, int | float):
            return raw, None
        nums = list(numbers_in(str(raw)))
        if len(nums) != 1:
            return None, "not a single number"
        n = float(nums[0])
        return (int(n) if n.is_integer() and "." not in nums[0] else n), None
    if ftype == "boolean":
        if isinstance(raw, bool):
            return raw, None
        s = str(raw).strip().lower()
        if s in ("yes", "true", "y"):
            return True, None
        if s in ("no", "false", "n"):
            return False, None
        return None, "not yes/no"
    options = [str(o) for o in (fd.get("options") or [])]
    lookup = {o.strip().lower(): o for o in options}
    if ftype == "choice":
        hit = lookup.get(str(raw).strip().lower())
        return (hit, None) if hit else (None, "not one of the options")
    if ftype == "multi_choice":
        items = raw if isinstance(raw, list) else re.split(r"[;\n]", str(raw))
        picked = []
        for it in items:
            if not str(it).strip():
                continue
            hit = lookup.get(str(it).strip().lower())
            if hit is None:
                return None, f"'{str(it).strip()[:40]}' is not one of the options"
            if hit not in picked:
                picked.append(hit)
        return (picked, None) if picked else (None, "no option selected")
    s = str(raw).strip()
    if len(s) > MAX_VALUE_CHARS:
        return None, "value too long"
    return s, None


# ---------- validation of one response ----------


def validate_answer(
    fields: list[dict[str, Any]], chunk: list[PageText], data: dict[str, Any], chunk_index: int = 0
) -> tuple[dict[str, Outcome], list[str]]:
    """Validate the model's JSON for one request. Returns ({field_key: Outcome}, problems)."""
    by_key = {fd["field_key"]: fd for fd in fields}
    pages = {p.page_no: p for p in chunk}
    out: dict[str, Outcome] = {}
    problems: list[str] = []
    answers = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(answers, list):
        return {}, ["response has no 'fields' list"]
    for a in answers:
        if not isinstance(a, dict):
            continue
        key = str(a.get("field_key") or "")
        if key not in by_key:
            problems.append(f"unknown field '{key[:40]}' ignored")
            continue
        if key in out:
            continue
        status = str(a.get("status") or "")
        note = (str(a.get("note") or "").strip() or None) if a.get("note") else None
        note = note[:MAX_NOTE_CHARS] if note else None
        if status == "not_located":
            out[key] = Outcome("not_located", note=note, chunk_index=chunk_index)
            continue
        quote = str(a.get("quote") or "")[:MAX_QUOTE_CHARS]
        page_no = a.get("page") if isinstance(a.get("page"), int) else None
        located: tuple[str, PageText] | None = None
        warnings: list[dict[str, Any]] = []
        if quote.strip():
            first = pages.get(page_no) if page_no is not None else None
            if first is not None and (ex := locate_quote(first.text, quote)):
                located = (ex, first)
            else:
                for p in chunk:
                    if ex := locate_quote(p.text, quote):
                        located = (ex, p)
                        warnings.append(
                            {
                                "code": "page_corrected",
                                "message": f"The model cited page {page_no}; the quote is on page {p.page_no}.",
                            }
                        )
                        break
        if status == "ambiguous":
            o = Outcome("ambiguous", note=note or "The model reported the text as ambiguous.", chunk_index=chunk_index)
            if located:
                o.excerpt, o.page_id, o.page_no = located[0], located[1].page_id, located[1].page_no
            out[key] = o
            continue
        if status != "found":
            problems.append(f"field '{key}': unknown status '{status[:20]}'")
            continue
        if located is None:
            out[key] = Outcome(
                "rejected",
                warnings=[
                    {
                        "code": "quote_not_in_source",
                        "message": "An AI proposal was discarded: its quote was not found in the page text.",
                    }
                ],
                chunk_index=chunk_index,
            )
            continue
        value, problem = coerce_value(by_key[key], a.get("value"))
        if problem:
            out[key] = Outcome(
                "rejected",
                warnings=[{"code": "invalid_value", "message": f"An AI proposal was discarded: {problem}."}],
                chunk_index=chunk_index,
            )
            continue
        excerpt, page = located
        num_w = numeric_warnings(value, excerpt)
        if by_key[key].get("type") == "number" and num_w:
            out[key] = Outcome(
                "rejected",
                warnings=[
                    {
                        "code": "number_not_in_excerpt",
                        "message": "An AI proposal was discarded: its number is not in the quoted text.",
                    }
                ],
                chunk_index=chunk_index,
            )
            continue
        out[key] = Outcome(
            "found",
            value=value,
            excerpt=excerpt,
            page_id=page.page_id,
            page_no=page.page_no,
            note=note,
            warnings=warnings + num_w,
            chunk_index=chunk_index,
        )
    return out, problems


# ---------- merging across requests ----------


def _same(a: Any, b: Any) -> bool:
    return _value_text(a).strip().lower() == _value_text(b).strip().lower()


def merge(per_chunk: list[dict[str, Outcome]], field_keys: list[str], answered_chunks: int) -> dict[str, Outcome]:
    """One outcome per field across all requests for a paper. A field with no answer in some request is left out
    (stays pending) unless another request found it; `not_located` needs a `not_located` answer from EVERY request."""
    merged: dict[str, Outcome] = {}
    for key in field_keys:
        answers = [c[key] for c in per_chunk if key in c]
        found = [o for o in answers if o.status == "found"]
        if found:
            distinct = [found[0]]
            for o in found[1:]:
                if not any(_same(o.value, d.value) for d in distinct):
                    distinct.append(o)
            if len(distinct) == 1:
                merged[key] = found[0]
            else:
                listing = "; ".join(f"'{_value_text(d.value)[:60]}' (page {d.page_no})" for d in distinct[:5])
                merged[key] = Outcome(
                    "ambiguous",
                    excerpt=found[0].excerpt,
                    page_id=found[0].page_id,
                    page_no=found[0].page_no,
                    note=f"Different values were found in the text: {listing}.",
                    warnings=[{"code": "conflicting_values", "message": "The text gives different values."}],
                    chunk_index=found[0].chunk_index,
                )
            continue
        amb = [o for o in answers if o.status == "ambiguous"]
        if amb:
            merged[key] = amb[0]
            continue
        rejected = [o for o in answers if o.status == "rejected"]
        if rejected:
            merged[key] = Outcome("rejected", warnings=rejected[0].warnings, chunk_index=rejected[0].chunk_index)
            continue
        nl = [o for o in answers if o.status == "not_located"]
        if nl and len(nl) == answered_chunks:
            merged[key] = Outcome("not_located", note=nl[0].note, chunk_index=nl[0].chunk_index)
    return merged


# ---------- schema versions ----------


def schema_diff(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Field keys added, removed, and changed (type, options or label) between two schema versions."""
    o = {f["field_key"]: f for f in old}
    n = {f["field_key"]: f for f in new}

    def sig(f: dict[str, Any]) -> tuple[Any, ...]:
        return (f.get("type") or "text", tuple(f.get("options") or ()), f.get("label"), f.get("hint"))

    return {
        "added": [k for k in n if k not in o],
        "removed": [k for k in o if k not in n],
        "changed": [k for k in n if k in o and sig(n[k]) != sig(o[k])],
    }
