"""Test doubles shared by unit and integration tests."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.llm.base import LLMError, LLMResult
from app.providers.scholarly.http import HttpFetcher
from app.providers.scholarly.throttle import LocalThrottle


def no_wait_fetcher(transport: httpx.BaseTransport, *, max_retries: int = 2) -> HttpFetcher:
    """An HttpFetcher that never sleeps (no throttle spacing, no backoff delay)."""
    return HttpFetcher(
        httpx.Client(transport=transport),
        LocalThrottle(sleep=lambda _s: None),
        max_retries=max_retries,
        sleep=lambda _s: None,
    )


class FakeLLM:
    """LLMProvider double. `answer(key, prompt_block) -> dict | None` builds one assessment per record key found in
    the prompt (None = leave that record out). `error` makes every call raise."""

    name = "fake"

    def __init__(
        self,
        answer: Callable[[str, str], dict[str, Any] | None] | None = None,
        *,
        error: LLMError | None = None,
        model: str = "fake-model-1",
    ):
        self.answer, self.error, self.model = answer, error, model
        self.prompts: list[str] = []

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], temperature: float, max_output_tokens: int
    ) -> LLMResult:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        blocks = re.split(r"^--- (P\d+)$", prompt, flags=re.M)
        items = []
        for key, block in zip(blocks[1::2], blocks[2::2], strict=True):
            a = self.answer(key, block) if self.answer else default_answer(key, block)
            if a is not None:
                items.append(a)
        return LLMResult("fake", self.model, {"assessments": items}, input_tokens=100, output_tokens=50, duration_ms=5)


def default_answer(key: str, block: str) -> dict[str, Any]:
    return {
        "paper": key,
        "relevance": 80,
        "relevance_reason": "Addresses the question.",
        "insufficient_information": False,
        "eligibility_suggestion": "include",
        "criteria": [],
    }


class FakeOcr:
    """OcrEngine double: returns canned text per rendered page (the renderer names files page-<n>.png)."""

    name = "fake"

    def __init__(self, texts: dict[int, str] | None = None, *, available: bool = True, confidence: float | None = 91.5):
        self.texts, self._available, self.confidence = texts or {}, available, confidence
        self.calls: list[int] = []

    def available(self) -> bool:
        return self._available

    def recognize(self, image):  # noqa: ANN001
        from app.documents.ocr import OcrResult

        n = int(re.search(r"page-(\d+)", str(image)).group(1))  # type: ignore[union-attr]
        self.calls.append(n)
        return OcrResult(self.texts.get(n, ""), self.confidence)


class FakeScanner:
    """MalwareScanner double."""

    name = "fake"

    def __init__(self, status: str = "clean", detail: str | None = None):
        self.status, self.detail = status, detail
        self.scanned = 0

    def scan(self, path):  # noqa: ANN001
        from app.providers.malware import ScanResult

        self.scanned += 1
        return ScanResult(self.status, self.detail)  # type: ignore[arg-type]
