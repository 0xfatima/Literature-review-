"""Request spacing: in-process and Redis-backed (with a fake Redis) and the Redis-down fallback."""

from app.providers.scholarly.throttle import LocalThrottle, RedisThrottle


class Clock:
    def __init__(self):
        self.t = 100.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def test_local_throttle_spaces_requests_per_key():
    c = Clock()
    th = LocalThrottle(clock=c.now, sleep=c.sleep)
    th.wait("a", 3.0)
    th.wait("a", 3.0)
    th.wait("b", 3.0)  # other keys are independent
    th.wait("a", 0)  # no spacing requested
    assert c.slept == [3.0]


class FakeRedis:
    def __init__(self, fail=False):
        self.keys: dict[str, int] = {}
        self.fail = fail

    def set(self, key, value, nx, px):
        if self.fail:
            raise ConnectionError("down")
        if key in self.keys:
            return False
        self.keys[key] = px
        return True

    def pttl(self, key):
        return self.keys.get(key, -2)


def test_redis_throttle_takes_the_slot_or_waits_for_its_ttl():
    r, slept = FakeRedis(), []

    def sleep(s):
        slept.append(s)
        r.keys.clear()  # the key expires while we sleep

    th = RedisThrottle(r, sleep=sleep)
    th.wait("arxiv", 3.0)
    th.wait("arxiv", 3.0)
    assert slept == [3.0]


def test_redis_down_falls_back_to_local_spacing():
    th = RedisThrottle(FakeRedis(fail=True), sleep=lambda s: None)
    th.wait("x", 0.01)  # must not raise
    assert th._warned
