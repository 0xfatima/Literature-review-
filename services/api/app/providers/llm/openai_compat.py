"""OpenAI-compatible chat completions (`POST {base}/chat/completions`): OpenAI, OpenRouter, Groq.

Structured output: the schema is appended to the system message and `response_format: {"type": "json_object"}` is
requested. Some models (notably several free OpenRouter models) reject `response_format`; then the request is
repeated once without it and the JSON is extracted from the text. The answer is always parsed and validated by the
caller; the schema is a request, not a guarantee.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.llm._http import post_json
from app.providers.llm.base import LLMError, LLMResult
from app.providers.llm.jsonutil import json_instructions, parse_json_object
from app.providers.scholarly.throttle import Throttle


class OpenAICompatProvider:
    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.Client,
        throttle: Throttle,
        throttle_key: str | None = None,
        min_interval_s: float = 1.0,
        max_retries: int = 3,
        extra_headers: dict[str, str] | None = None,
        json_mode: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key or not model:
            raise LLMError("not_configured", "An API key and a model must both be set.")
        self.name, self._key, self.model, self._client, self._throttle = name, api_key, model, client, throttle
        self._base = base_url.rstrip("/")
        self._tkey = throttle_key or f"llm:{name}:{model}"
        self._interval, self._retries, self._sleep = min_interval_s, max_retries, sleep
        self._extra = dict(extra_headers or {})
        self._json_mode = json_mode

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], temperature: float, max_output_tokens: int
    ) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system + json_instructions(schema)},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json", **self._extra}
        start = time.monotonic()
        common = dict(
            client=self._client,
            url=f"{self._base}/chat/completions",
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
            resp, attempts = post_json(body=body, **common)  # type: ignore[arg-type]
        except LLMError as e:
            if not (e.kind == "rejected" and e.status in (400, 422) and "response_format" in body):
                raise
            body.pop("response_format")  # the model does not support JSON mode: ask in the prompt only
            resp, attempts = post_json(body=body, **common)  # type: ignore[arg-type]
        return self._parse(resp, attempts, start)

    def _parse(self, resp: httpx.Response, attempts: int, start: float) -> LLMResult:
        try:
            body = resp.json()
        except ValueError:
            raise LLMError("malformed", "The AI provider returned a response that is not JSON.") from None
        if not isinstance(body, dict):
            raise LLMError("malformed", "The AI provider returned an unexpected response.")
        if isinstance(body.get("error"), dict):  # OpenRouter reports some upstream failures with HTTP 200
            msg = str(body["error"].get("message") or "error")[:200]
            raise LLMError("server_error", f"The AI provider reported an error: {msg}")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError("blocked", "The AI provider returned no answer.")
        choice = choices[0]
        finish = choice.get("finish_reason")
        message: dict[str, Any] = choice["message"] if isinstance(choice.get("message"), dict) else {}
        text = message.get("content") or ""
        if isinstance(text, list):  # some gateways return content parts
            text = "".join(str(p.get("text", "")) for p in text if isinstance(p, dict))
        usage: dict[str, Any] = body["usage"] if isinstance(body.get("usage"), dict) else {}
        result = LLMResult(
            provider=self.name,
            model=str(body.get("model") or self.model),
            data=None,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            duration_ms=int((time.monotonic() - start) * 1000),
            attempts=attempts,
            finish_reason=str(finish) if finish else None,
            raw_text=str(text)[:200_000],
        )
        if finish == "length":
            raise LLMError("blocked", "The AI answer was cut off (finish_reason=length).")
        if finish == "content_filter":
            raise LLMError("blocked", "The AI answer was filtered by the provider.")
        result.data = parse_json_object(str(text))
        return result
