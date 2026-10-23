"""HTTP for scholarly providers: timeouts, bounded retries with exponential backoff, Retry-After, size limits and
per-provider request spacing. The httpx client is injected, so tests use httpx.MockTransport and never the network.

Retried: timeouts, connection errors, HTTP 429 and 5xx. Not retried: other 4xx (the provider rejected the request;
retrying cannot help) and malformed bodies. Every failure becomes a ProviderError with a short, user-safe message;
response bodies and request secrets are never put into messages or logs.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.providers.scholarly.throttle import Throttle

log = logging.getLogger(__name__)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
SECRET_PARAMS = frozenset({"api_key", "mailto", "email", "apikey", "key"})


class ProviderError(Exception):
    """kind: timeout | network | rate_limited | server_error | rejected | malformed | too_large"""

    def __init__(self, provider: str, kind: str, message: str, *, status: int | None = None, attempts: int = 1):
        super().__init__(message)
        self.provider, self.kind, self.message, self.status, self.attempts = provider, kind, message, status, attempts

    def summary(self) -> str:
        suffix = f" after {self.attempts} attempts" if self.attempts > 1 else ""
        return f"{self.message}{suffix}"


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form: fall back to our own backoff


class HttpFetcher:
    def __init__(
        self,
        client: httpx.Client,
        throttle: Throttle,
        *,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        max_backoff_s: float = 30.0,
        max_bytes: int = 25_000_000,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client, self.throttle = client, throttle
        self.max_retries, self.backoff_base_s, self.max_backoff_s = max_retries, backoff_base_s, max_backoff_s
        self.max_bytes, self.sleep = max_bytes, sleep
        self.requests_made = 0

    def _backoff(self, attempt: int, hint: float | None) -> float:
        if hint is not None:
            return float(min(hint, self.max_backoff_s))
        jitter = random.uniform(0, self.backoff_base_s)  # noqa: S311  (not security relevant)
        return float(min(self.backoff_base_s * 2.0 ** (attempt - 1) + jitter, self.max_backoff_s))

    def get(self, provider: str, url: str, params: dict[str, Any], *, min_interval_s: float) -> httpx.Response:
        attempts = self.max_retries + 1
        last: ProviderError | None = None
        for attempt in range(1, attempts + 1):
            self.throttle.wait(f"scholarly:{provider}", min_interval_s)
            self.requests_made += 1
            try:
                resp = self.client.get(url, params=params)
            except httpx.TimeoutException:
                last = ProviderError(provider, "timeout", f"{provider} did not respond in time", attempts=attempt)
                hint = None
            except httpx.TransportError as e:
                last = ProviderError(
                    provider, "network", f"could not reach {provider} ({type(e).__name__})", attempts=attempt
                )
                hint = None
            else:
                if resp.status_code < 400:
                    if len(resp.content) > self.max_bytes:
                        raise ProviderError(provider, "too_large", f"{provider} response was too large")
                    return resp
                if resp.status_code not in RETRYABLE_STATUS:
                    raise ProviderError(
                        provider,
                        "rejected",
                        f"{provider} rejected the request (HTTP {resp.status_code})",
                        status=resp.status_code,
                        attempts=attempt,
                    )
                kind = "rate_limited" if resp.status_code == 429 else "server_error"
                what = "rate limit reached" if kind == "rate_limited" else f"server error (HTTP {resp.status_code})"
                last = ProviderError(provider, kind, f"{provider}: {what}", status=resp.status_code, attempts=attempt)
                hint = _retry_after(resp)
            if attempt < attempts:
                delay = self._backoff(attempt, hint)
                log.info(
                    "provider request retry",
                    extra={"provider": provider, "kind": last.kind, "attempt": attempt, "delay_s": round(delay, 2)},
                )
                self.sleep(delay)
        assert last is not None
        raise last

    def get_json(self, provider: str, url: str, params: dict[str, Any], *, min_interval_s: float) -> Any:
        resp = self.get(provider, url, params, min_interval_s=min_interval_s)
        try:
            return resp.json()
        except ValueError:
            raise ProviderError(provider, "malformed", f"{provider} returned a response that is not JSON") from None

    def get_text(self, provider: str, url: str, params: dict[str, Any], *, min_interval_s: float) -> str:
        return self.get(provider, url, params, min_interval_s=min_interval_s).text


def public_url(endpoint: str, params: dict[str, Any]) -> str:
    """A reproducible URL for display: secrets and contact details removed."""
    safe = {k: v for k, v in params.items() if k.lower() not in SECRET_PARAMS}
    return str(httpx.URL(endpoint, params=safe))
