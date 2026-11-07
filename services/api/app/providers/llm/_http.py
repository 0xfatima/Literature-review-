"""Shared POST-with-retries for the HTTP-based AI providers (OpenAI-compatible, Anthropic).

Retried: timeouts, connection errors, 429 and 5xx (Retry-After honoured). Not retried: other 4xx. The throttle key
includes a fingerprint of the API key, so users with their own keys do not slow each other down, while every worker
using the SAME key shares one request spacing and one 429 back-off.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.llm.base import LLMError
from app.providers.scholarly.throttle import Throttle, penalize

RETRYABLE = frozenset({429, 500, 502, 503, 504, 529})


def _retry_after(resp: httpx.Response) -> float | None:
    for h in ("retry-after", "x-ratelimit-reset-requests"):
        v = resp.headers.get(h)
        if v:
            try:
                return float(v.rstrip("s"))
            except ValueError:
                continue
    return None


def rejected_message(provider: str, model: str, status: int) -> str:
    what = {
        400: "rejected the request",
        401: "rejected the API key",
        402: "requires credit on this account (the model is not free, or the free quota is used up)",
        403: "refused access (check the API key and its permissions)",
        404: f"does not offer model {model!r}",
    }.get(status, f"returned HTTP {status}")
    return f"The AI provider ({provider}) {what}."


def post_json(
    *,
    client: httpx.Client,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    throttle: Throttle,
    throttle_key: str,
    min_interval_s: float,
    max_retries: int,
    provider: str,
    model: str,
    max_backoff_s: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[httpx.Response, int]:
    """Returns (successful response, attempts). Raises LLMError."""
    last: LLMError | None = None
    for attempt in range(1, max_retries + 2):
        throttle.wait(throttle_key, min_interval_s)
        hint: float | None = None
        try:
            resp = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException:
            last = LLMError("timeout", "The AI model did not respond in time.")
        except httpx.TransportError as e:
            last = LLMError("network", f"Could not reach the AI provider ({type(e).__name__}).")
        else:
            if resp.status_code < 400:
                return resp, attempt
            if resp.status_code not in RETRYABLE:
                raise LLMError("rejected", rejected_message(provider, model, resp.status_code), status=resp.status_code)
            rate = resp.status_code == 429
            last = LLMError(
                "rate_limited" if rate else "server_error",
                "The AI provider's rate limit or daily quota was reached."
                if rate
                else f"The AI provider had a server error (HTTP {resp.status_code}).",
                status=resp.status_code,
            )
            hint = _retry_after(resp)
            if rate:
                penalize(throttle, throttle_key, hint if hint is not None else 2.0**attempt)
        if attempt <= max_retries:
            base = 2.0 ** (attempt - 1) + random.uniform(0, 1)  # noqa: S311
            sleep(float(min(hint if hint is not None else base, max_backoff_s)))
    assert last is not None
    raise last
