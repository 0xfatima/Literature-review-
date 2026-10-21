"""Fixed-window rate limiting behind an interface. In-memory impl is stdlib-only (tests, single process);
RedisRateLimiter is the shared implementation used when running multiple API replicas."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol


class RateLimiter(Protocol):
    def hit(self, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        """Record an attempt. Returns (allowed, retry_after_seconds)."""
        ...


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._windows: dict[str, tuple[float, int]] = {}

    def hit(self, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        now = self._clock()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= window_s:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)
        if count > limit:
            return False, max(1, int(window_s - (now - start)) + 1)
        return True, 0


class RedisRateLimiter:
    """INCR + EXPIRE fixed window. Fails OPEN on Redis errors (availability over lockout) but logs;
    this trade-off is documented in the ADR amendment."""

    def __init__(self, client: Any, prefix: str = "rl:"):  # client: redis.Redis
        self._r, self._p = client, prefix

    def hit(self, key: str, limit: int, window_s: int) -> tuple[bool, int]:
        k = f"{self._p}{key}"
        try:
            pipe = self._r.pipeline()
            pipe.incr(k)
            pipe.ttl(k)
            count, ttl = pipe.execute()
            if ttl is None or ttl < 0:
                self._r.expire(k, window_s)
                ttl = window_s
            if count > limit:
                return False, int(ttl) or 1
            return True, 0
        except Exception:  # noqa: BLE001 - deliberate fail-open, see class docstring
            import logging
            logging.getLogger(__name__).warning("rate limiter backend unavailable; failing open")
            return True, 0
