"""Helpers for providers without Gemini-style `responseSchema`: schema conversion and lenient JSON extraction."""

from __future__ import annotations

import json
import re
from typing import Any

from app.providers.llm.base import LLMError

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", re.S)


def to_json_schema(schema: Any) -> Any:
    """Gemini's OpenAPI subset ("type": "OBJECT") -> standard JSON Schema ("type": "object")."""
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k == "type" and isinstance(v, str):
                out[k] = v.lower()
            elif k == "nullable":
                continue
            else:
                out[k] = to_json_schema(v)
        return out
    if isinstance(schema, list):
        return [to_json_schema(x) for x in schema]
    return schema


def json_instructions(schema: dict[str, Any]) -> str:
    return (
        "\n\nRespond with ONE JSON object and nothing else (no prose, no Markdown code fences). It must conform to "
        "this JSON Schema:\n" + json.dumps(to_json_schema(schema), separators=(",", ":"))
    )


def parse_json_object(text: str) -> dict[str, Any]:
    """Accepts a bare object, an object inside ```json fences, or an object with stray text around it."""
    s = text.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1)
    try:
        data = json.loads(s)
    except ValueError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("malformed", "The AI answer was not valid JSON.") from None
        try:
            data = json.loads(s[start : end + 1])
        except ValueError:
            raise LLMError("malformed", "The AI answer was not valid JSON.") from None
    if not isinstance(data, dict):
        raise LLMError("malformed", "The AI answer was not a JSON object.")
    return data
