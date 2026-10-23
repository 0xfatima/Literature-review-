"""LLM provider interface and gateway (spec: "wrap the LLM behind a provider interface").

Only `LLMGateway` is used by services. It enforces the data-handling policy
(docs/architecture/provider-data-handling.md):
  * `public_metadata` (titles, abstracts, bibliographic fields, protocol text) may be sent when an LLM is configured;
  * `private_document` (uploaded PDFs and text derived from them) is refused unless LLM_SEND_PRIVATE_DOCUMENTS=true
    (and the consent gates of later phases pass). Phase 5 sends public metadata only.
Every call is described by an LLMResult that the caller stores in `model_runs` (provider, model, prompt version,
tokens, duration, status), success or failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

DATA_CLASSES = ("public_metadata", "private_document")


class LLMError(Exception):
    """kind: not_configured | refused | timeout | network | rate_limited | server_error | rejected | malformed |
    blocked (safety filter / no candidate). Message is short and user-safe; never contains the API key."""

    def __init__(self, kind: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


@dataclass
class LLMResult:
    provider: str
    model: str
    data: dict[str, Any] | None  # parsed JSON object, when the call succeeded
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int = 0
    attempts: int = 1
    finish_reason: str | None = None
    raw_text: str | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], temperature: float, max_output_tokens: int
    ) -> LLMResult:
        """Return a JSON object conforming (as far as the provider supports) to `schema`. Raises LLMError."""
        ...


@dataclass
class LLMGateway:
    provider: LLMProvider | None
    send_private_documents: bool = False
    calls: list[LLMResult] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.provider is not None

    def generate_json(
        self,
        *,
        data_class: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        temperature: float = 0.0,
        max_output_tokens: int = 4096,
    ) -> LLMResult:
        if data_class not in DATA_CLASSES:
            raise LLMError("refused", f"unknown data class {data_class!r}")
        if self.provider is None:
            raise LLMError("not_configured", "No AI model is configured on this server.")
        if data_class == "private_document" and not self.send_private_documents:
            raise LLMError("refused", "Sending private documents to an AI provider is disabled on this server.")
        start = time.monotonic()
        result = self.provider.generate_json(
            system=system, prompt=prompt, schema=schema, temperature=temperature, max_output_tokens=max_output_tokens
        )
        result.duration_ms = result.duration_ms or int((time.monotonic() - start) * 1000)
        self.calls.append(result)
        return result
