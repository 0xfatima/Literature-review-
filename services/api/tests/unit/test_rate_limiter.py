# stdlib-only
from app.core.rate_limit import InMemoryRateLimiter


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_allows_up_to_limit_then_blocks_with_retry_after():
    c = Clock()
    rl = InMemoryRateLimiter(c)
    assert all(rl.hit("k", 3, 60)[0] for _ in range(3))
    ok, retry = rl.hit("k", 3, 60)
    assert not ok and 1 <= retry <= 61


def test_window_resets_and_keys_are_independent():
    c = Clock()
    rl = InMemoryRateLimiter(c)
    for _ in range(4):
        rl.hit("a", 3, 60)
    assert rl.hit("b", 3, 60)[0]
    c.t = 61
    assert rl.hit("a", 3, 60)[0]
