# stdlib-only
import os
import tempfile
import time
from pathlib import Path

from app.core import shutdown
from app.providers.scholarly.throttle import LocalThrottle, penalize

ORIGINAL_DIR = shutdown._DIR


def _isolated() -> Path:
    shutdown._DIR = Path(tempfile.mkdtemp())
    return shutdown._DIR


def test_drain_flag_is_seen_by_this_process_and_cleared():
    _isolated()
    assert not shutdown.draining() and not shutdown.past(time.monotonic() + 60)
    shutdown.begin_drain()
    try:
        assert shutdown.draining() and shutdown.past(time.monotonic() + 60)
        assert (shutdown._DIR / f"srs-worker-drain-{os.getpid()}").exists()
    finally:
        shutdown.end_drain()
    assert not shutdown.draining()
    shutdown._DIR = ORIGINAL_DIR


def test_child_sees_parents_drain_flag():
    flag = _isolated() / f"srs-worker-drain-{os.getppid()}"
    flag.write_text("1")  # what the worker main process writes
    try:
        assert shutdown.draining()
    finally:
        flag.unlink()
        shutdown._DIR = ORIGINAL_DIR


def test_past_is_true_after_the_deadline():
    assert shutdown.past(time.monotonic() - 1)


def test_penalize_makes_the_next_wait_honour_the_cooldown():
    slept: list[float] = []
    now = [100.0]
    t = LocalThrottle(clock=lambda: now[0], sleep=slept.append)
    penalize(t, "k", 5.0)
    t.wait("k", 0.1)
    assert slept and abs(slept[0] - 5.0) < 1e-9


def test_penalize_ignores_throttles_without_support_and_bad_values():
    class Plain:
        def wait(self, key, min_interval_s):
            raise AssertionError("not called")

    penalize(Plain(), "k", 3)  # no penalize(): left alone
    t = LocalThrottle(sleep=lambda _s: (_ for _ in ()).throw(AssertionError("no sleep expected")))
    penalize(t, "k", None)
    penalize(t, "k", 0)
    t.wait("k", 0)  # nothing scheduled
