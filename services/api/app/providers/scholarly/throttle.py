"""Minimum spacing between requests to one provider, shared by every worker process.

RedisThrottle: `SET key 1 NX PX <interval>` succeeds for exactly one caller per interval; others sleep for the key's
remaining TTL and try again. This enforces "no more than one request per interval" across processes and hosts
without a coordinator. If Redis is unavailable it falls back to the in-process throttle (still spacing this
process's requests) and logs once, rather than failing the search.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Throttle(Protocol):
    def wait(self, key: str, min_interval_s: float) -> None: ...


class LocalThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self._clock, self._sleep = clock, sleep
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, min_interval_s: float) -> None:
        if min_interval_s <= 0:
            return
        with self._lock:
            now = self._clock()
            start = max(now, self._next.get(key, now))
            self._next[key] = start + min_interval_s
        if start > now:
            self._sleep(start - now)


class RedisThrottle:
    def __init__(self, client: Any, *, sleep: Callable[[float], None] = time.sleep, max_wait_s: float = 120.0):
        self._r, self._sleep, self._max_wait = client, sleep, max_wait_s
        self._fallback = LocalThrottle()
        self._warned = False

    def wait(self, key: str, min_interval_s: float) -> None:
        if min_interval_s <= 0:
            return
        ms = max(1, int(min_interval_s * 1000))
        waited = 0.0
        try:
            while waited < self._max_wait:
                if self._r.set(f"throttle:{key}", "1", nx=True, px=ms):
                    return
                ttl = self._r.pttl(f"throttle:{key}")
                delay = (ttl if isinstance(ttl, int) and ttl > 0 else ms) / 1000
                self._sleep(delay)
                waited += delay
            return  # never block a job forever on a stuck key; the provider's own 429 handling still applies
        except Exception:  # noqa: BLE001  degrade to per-process spacing instead of failing the search
            if not self._warned:
                log.warning("redis throttle unavailable; using per-process request spacing")
                self._warned = True
            self._fallback.wait(key, min_interval_s)
