"""Google Gemini API adapter (generativelanguage.googleapis.com, `generateContent`).

Structured output: `responseMimeType: application/json` + `responseSchema` (OpenAPI subset). The response is still
parsed and validated by the caller; the schema is a request, not a guarantee. The API key is sent in the
`x-goog-api-key` header (never in the URL, so it cannot leak into logs of request URLs).

Retries: timeouts, connection errors, 429 and 5xx with exponential backoff (Retry-After / RetryInfo honoured when
present). 400/401/403/404 are not retried: they mean a bad request, a bad key or an unknown model.
Free-tier note: on Google's free tier, submitted content may be used to improve Google's products. This phase only
sends public metadata (titles, abstracts) and the protocol; the UI says so before an assessment is started.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.llm.base import LLMError, LLMResult
from app.providers.scholarly.throttle import Throttle

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE = frozenset({429, 500, 502, 503, 504})


def _retry_delay(resp: httpx.Response) -> float | None:
    h = resp.headers.get("retry-after")
    if h:
        try:
            return float(h)
        except ValueError:
            pass
    try:  # google.rpc.RetryInfo: {"@type": ".../RetryInfo", "retryDelay": "12s"}
        for d in resp.json().get("error", {}).get("details", []) or []:
            delay = str(d.get("retryDelay") or "")
            if delay.endswith("s"):
                return float(delay[:-1])
    except (ValueError, AttributeError):
        pass
    return None


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.Client,
        throttle: Throttle,
        base_url: str = DEFAULT_BASE_URL,
        min_interval_s: float = 4.5,
        max_retries: int = 3,
        max_backoff_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key or not model:
            raise LLMError("not_configured", "LLM_API_KEY and LLM_MODEL must both be set.")
        self._key, self.model, self._client, self._throttle = api_key, model, client, throttle
        self._base = base_url.rstrip("/")
        self._interval, self._retries, self._max_backoff, self._sleep = (
            min_interval_s,
            max_retries,
            max_backoff_s,
            sleep,
        )

    def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], temperature: float, max_output_tokens: int
    ) -> LLMResult:
        url = f"{self._base}/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        headers = {"x-goog-api-key": self._key, "Content-Type": "application/json"}
        start = time.monotonic()
        last: LLMError | None = None
        for attempt in range(1, self._retries + 2):
            self._throttle.wait(f"llm:{self.name}:{self.model}", self._interval)
            hint: float | None = None
            try:
                resp = self._client.post(url, json=body, headers=headers)
            except httpx.TimeoutException:
                last = LLMError("timeout", "The AI model did not respond in time.")
            except httpx.TransportError as e:
                last = LLMError("network", f"Could not reach the AI provider ({type(e).__name__}).")
            else:
                if resp.status_code < 400:
                    return self._parse(resp, attempt, start)
                if resp.status_code not in RETRYABLE:
                    what = {
                        400: "rejected the request",
                        401: "rejected the API key",
                        403: "refused access (check the API key and its permissions)",
                        404: f"does not offer model {self.model!r}",
                    }.get(resp.status_code, f"returned HTTP {resp.status_code}")
                    raise LLMError("rejected", f"The AI provider {what}.", status=resp.status_code)
                kind = "rate_limited" if resp.status_code == 429 else "server_error"
                last = LLMError(
                    kind,
                    "The AI provider's rate limit or daily quota was reached."
                    if kind == "rate_limited"
                    else f"The AI provider had a server error (HTTP {resp.status_code}).",
                    status=resp.status_code,
                )
                hint = _retry_delay(resp)
            if attempt <= self._retries:
                base = 2.0 ** (attempt - 1) + random.uniform(0, 1)  # noqa: S311
                self._sleep(float(min(hint if hint is not None else base, self._max_backoff)))
        assert last is not None
        raise last

    def _parse(self, resp: httpx.Response, attempts: int, start: float) -> LLMResult:
        try:
            body = resp.json()
        except ValueError:
            raise LLMError("malformed", "The AI provider returned a response that is not JSON.") from None
        usage = body.get("usageMetadata") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        cands = body.get("candidates") if isinstance(body, dict) else None
        if not isinstance(cands, list) or not cands:
            reason = (body.get("promptFeedback") or {}).get("blockReason") if isinstance(body, dict) else None
            raise LLMError("blocked", f"The AI provider returned no answer{f' ({reason})' if reason else ''}.")
        cand = cands[0] if isinstance(cands[0], dict) else {}
        finish = cand.get("finishReason")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        result = LLMResult(
            provider=self.name,
            model=str(body.get("modelVersion") or self.model),
            data=None,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            duration_ms=int((time.monotonic() - start) * 1000),
            attempts=attempts,
            finish_reason=str(finish) if finish else None,
            raw_text=text[:200_000],
        )
        if finish not in (None, "STOP"):
            raise LLMError("blocked", f"The AI answer was cut off or filtered ({finish}).")
        try:
            data = json.loads(text)
        except ValueError:
            raise LLMError("malformed", "The AI answer was not valid JSON.") from None
        if not isinstance(data, dict):
            raise LLMError("malformed", "The AI answer was not a JSON object.")
        result.data = data
        return result
