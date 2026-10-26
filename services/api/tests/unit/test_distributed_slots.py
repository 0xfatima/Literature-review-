import threading

from app.core.distributed import RedisSemaphore, Slots


class Broken:
    def eval(self, *a, **k):
        raise ConnectionError("redis down")

    def zrem(self, *a):
        raise ConnectionError("redis down")


def test_redis_outage_degrades_to_the_local_limit_instead_of_blocking():
    s = RedisSemaphore(Broken(), "x", 1)
    assert s.acquire(timeout=0.1) and s.acquire(timeout=0.1)  # degraded: allowed
    s.release()
    s.release()  # no error


class Shared:
    def __init__(self, ok: bool):
        self.ok, self.acquired, self.released = ok, 0, 0

    def acquire(self, timeout=None):
        self.acquired += 1
        return self.ok

    def release(self):
        self.released += 1


def test_slots_hold_both_limits_and_give_back_the_local_slot_on_shared_refusal():
    local = threading.BoundedSemaphore(1)
    refused = Slots(local, Shared(False))
    assert not refused.acquire(timeout=0.01)
    ok = Slots(local, Shared(True))  # the local slot was returned above, so this can take it
    assert ok.acquire(timeout=0.01)
    assert not Slots(local, Shared(True)).acquire(timeout=0.01)  # local limit (1) now reached
    ok.release()
    assert ok.shared.released == 1 and local.acquire(timeout=0.01)
