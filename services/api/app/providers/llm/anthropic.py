"""Anthropic Messages API (`POST {base}/messages`). JSON is requested in the system prompt and extracted from text."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.llm._http import post_json
from app.providers.llm.base import LLMError, LLMResult
from app.providers.llm.jsonutil import json_instructions, parse_json_object
from app.providers.scholarly.throttle import Throttle

API_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.Client,
        throttle: Throttle,
        throttle_key: str | None = None,
        min_interval_s: float = 1.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key or not model:
            raise LLMError("not_configured", "An API key and a model must both be set.")
        self._key, self.model, self._client, self._throttle = api_key, model, client, throttle
        self._base = base_url.rstrip("/")
        self._tkey = throttle_key or f"llm:anthropic:{model}"
        self._interval, self._retries, self._sleep = min_interval_s, max_retries, sleep

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], temperature: float, max_output_tokens: int
    ) -> LLMResult:
        body = {
            "model": self.model,
            "system": system + json_instructions(schema),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "temperature": temperature,
        }
        headers = {"x-api-key": self._key, "anthropic-version": API_VERSION, "Content-Type": "application/json"}
        start = time.monotonic()
        resp, attempts = post_json(
            client=self._client,
            url=f"{self._base}/messages",
            body=body,
            headers=headers,
            throttle=self._throttle,
            throttle_key=self._tkey,
            min_interval_s=self._interval,
            max_retries=self._retries,
            provider=self.name,
            model=self.model,
            sleep=self._sleep,
        )
        try:
            data = resp.json()
        except ValueError:
            raise LLMError("malformed", "The AI provider returned a response that is not JSON.") from None
        if not isinstance(data, dict):
            raise LLMError("malformed", "The AI provider returned an unexpected response.")
        parts: list[Any] = data["content"] if isinstance(data.get("content"), list) else []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("type") == "text")
        usage: dict[str, Any] = data["usage"] if isinstance(data.get("usage"), dict) else {}
        stop = data.get("stop_reason")
        result = LLMResult(
            provider=self.name,
            model=str(data.get("model") or self.model),
            data=None,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            duration_ms=int((time.monotonic() - start) * 1000),
            attempts=attempts,
            finish_reason=str(stop) if stop else None,
            raw_text=text[:200_000],
        )
        if stop == "max_tokens":
            raise LLMError("blocked", "The AI answer was cut off (max_tokens).")
        if stop == "refusal":
            raise LLMError("blocked", "The AI model declined to answer.")
        result.data = parse_json_object(text)
        return result
