"""LLM provider interface and gateway (spec: "wrap the LLM behind a provider interface").

Only `LLMGateway` is used by services. It enforces the data-handling policy
(docs/architecture/provider-data-handling.md):
  * `public_metadata` (titles, abstracts, bibliographic fields, protocol text) may be sent when an LLM is configured;
  * `private_document` (uploaded PDFs and text derived from them) is refused unless ALL gates pass (Phase 9):
    G1 LLM_SEND_PRIVATE_DOCUMENTS=true, G2 a reviewed, unexpired provider terms record (`terms_reviewed`), and
    G3 a `ConsentToken` for this provider/model, built by the caller from an active `llm_consents` row.
Every call is described by an LLMResult that the caller stores in `model_runs` (provider, model, prompt version,
tokens, duration, status), success or failure.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
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


@dataclass(frozen=True)
class ConsentToken:
    """Proof (read from `llm_consents` by the caller) that the project owner consented to sending private document
    text to exactly this provider and model."""

    consent_id: str
    provider: str
    model: str


@dataclass
class LLMGateway:
    provider: LLMProvider | None
    send_private_documents: bool = False
    terms_reviewed: bool = False
    calls: list[LLMResult] = field(default_factory=list)
    # Phase 13 limits. `budget_guard` returns a reason when a token budget is spent (set by the calling job);
    # `slots` bounds concurrent calls in this process.
    budget_guard: Callable[[], str | None] | None = None
    slots: threading.BoundedSemaphore | None = None
    slot_wait_s: float = 60.0

    def private_document_block(self, consent: ConsentToken | None) -> str | None:
        """None when private document text may be sent; otherwise a short user-safe reason."""
        if self.provider is None:
            return "No AI model is configured on this server."
        if not self.send_private_documents:
            return (
                "Sending uploaded documents to an AI provider is disabled on this server (LLM_SEND_PRIVATE_DOCUMENTS)."
            )
        if not self.terms_reviewed:
            return "The AI provider's data-use terms have not been reviewed by the operator (LLM_TERMS_RECORD_*)."
        if consent is None or consent.provider != self.provider.name or consent.model != self.provider.model:
            return "The project owner has not consented to sending document text to this AI provider and model."
        return None

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
        consent: ConsentToken | None = None,
    ) -> LLMResult:
        if data_class not in DATA_CLASSES:
            raise LLMError("refused", f"unknown data class {data_class!r}")
        if self.provider is None:
            raise LLMError("not_configured", "No AI model is configured on this server.")
        if data_class == "private_document":
            block = self.private_document_block(consent)
            if block is not None:
                raise LLMError("refused", block)
        if self.budget_guard is not None:
            reason = self.budget_guard()
            if reason:
                raise LLMError("rate_limited", reason)
        if self.slots is not None and not self.slots.acquire(timeout=self.slot_wait_s):
            raise LLMError("rate_limited", "Too many AI calls are in progress; try again shortly.")
        start = time.monotonic()
        try:
            result = self.provider.generate_json(
                system=system,
                prompt=prompt,
                schema=schema,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        finally:
            if self.slots is not None:
                self.slots.release()
        result.duration_ms = result.duration_ms or int((time.monotonic() - start) * 1000)
        self.calls.append(result)
        return result
