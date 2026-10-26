"""Cross-process concurrency limit backed by Redis (Phase 15).

`RedisSemaphore` grants at most N concurrent leases across every API and worker process. A lease is a member of a
sorted set scored by its expiry; acquiring first drops expired members, so a crashed holder frees its slot after
`lease_s` without any coordinator. Acquire/release are single Lua scripts (atomic).

If Redis is unavailable the semaphore degrades to "allowed" and logs once: the per-process semaphore in front of it
still bounds concurrency, and the provider's own 429 handling plus the shared cooldown still apply. Failing closed
would stop every AI step during a Redis blip, while Redis being down also stops the job queue itself.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_ACQUIRE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
  redis.call('PEXPIRE', KEYS[1], ARGV[5])
  return 1
end
return 0
"""


class RedisSemaphore:
    def __init__(
        self,
        client: Any,
        name: str,
        limit: int,
        *,
        lease_s: float = 300.0,
        poll_s: float = 0.2,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._r, self._key, self._limit = client, f"sem:{name}", max(1, limit)
        self._lease, self._poll, self._clock, self._sleep = lease_s, poll_s, clock, sleep
        self._tokens = threading.local()
        self._warned = False

    def _stack(self) -> list[str | None]:
        if not hasattr(self._tokens, "stack"):
            self._tokens.stack = []
        stack: list[str | None] = self._tokens.stack
        return stack

    def acquire(self, timeout: float | None = None) -> bool:
        token = uuid.uuid4().hex
        deadline = None if timeout is None else self._clock() + timeout
        try:
            while True:
                now = self._clock()
                ok = self._r.eval(
                    _ACQUIRE,
                    1,
                    self._key,
                    now,
                    self._limit,
                    now + self._lease,
                    token,
                    int(self._lease * 1000) + 1000,
                )
                if int(ok) == 1:
                    self._stack().append(token)
                    return True
                if deadline is not None and self._clock() >= deadline:
                    return False
                self._sleep(self._poll)
        except Exception:  # noqa: BLE001 - degrade (see module docstring)
            if not self._warned:
                log.warning("redis semaphore unavailable; only the per-process limit applies", extra={"key": self._key})
                self._warned = True
            self._stack().append(None)
            return True

    def release(self) -> None:
        stack = self._stack()
        token = stack.pop() if stack else None
        if token is None:
            return
        try:
            self._r.zrem(self._key, token)
        except Exception:  # noqa: BLE001 - the lease expires on its own
            log.warning("redis semaphore release failed; the lease will expire", extra={"key": self._key})

    def in_use(self) -> int | None:
        try:
            self._r.zremrangebyscore(self._key, "-inf", self._clock())
            return int(self._r.zcard(self._key))
        except Exception:  # noqa: BLE001
            return None


class Slots:
    """Local (per-process) semaphore first, then the shared one: both limits always hold."""

    def __init__(self, local: threading.BoundedSemaphore, shared: RedisSemaphore | None):
        self.local, self.shared = local, shared

    def acquire(self, timeout: float | None = None) -> bool:
        start = time.monotonic()
        if not self.local.acquire(timeout=timeout):
            return False
        if self.shared is None:
            return True
        left = None if timeout is None else max(0.0, timeout - (time.monotonic() - start))
        if self.shared.acquire(timeout=left):
            return True
        self.local.release()
        return False

    def release(self) -> None:
        if self.shared is not None:
            self.shared.release()
        self.local.release()
