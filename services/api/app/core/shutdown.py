"""Graceful worker drain (Phase 15). Stdlib only.

Celery's warm shutdown (SIGTERM) waits for running tasks, but the orchestrator kills the container after its grace
period, and some jobs (search, extraction, relevance, appraisal) can run for many minutes. Those jobs already stop
cleanly at checkpoints when their time budget is used up, leaving resumable state. Draining reuses exactly those
checkpoints: once the worker is asked to stop, `past(deadline)` is true, so each job stops at its next checkpoint.

Celery signals fire in the worker's MAIN process while prefork tasks run in CHILD processes, so the flag is a sentinel
file named after the main process id; children check their parent's file (a stat per checkpoint). Anything that is
killed anyway is still safe: acks_late + reject_on_worker_lost redeliver the message and the sweeper recovers it.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_DIR = Path(os.environ.get("SRS_DRAIN_DIR") or tempfile.gettempdir())


def _flag(pid: int) -> Path:
    return _DIR / f"srs-worker-drain-{pid}"


def begin_drain() -> None:
    """Called in the worker main process when shutdown starts."""
    try:
        _flag(os.getpid()).write_text(str(time.time()))
    except OSError:  # a read-only tmp must not break shutdown; jobs then just run to their normal budget
        pass


def end_drain() -> None:
    try:
        _flag(os.getpid()).unlink(missing_ok=True)
    except OSError:
        pass


def draining() -> bool:
    return _flag(os.getppid()).exists() or _flag(os.getpid()).exists()


def past(deadline: float) -> bool:
    """True when a job must stop at this checkpoint: its time budget is used up or the worker is draining."""
    return time.monotonic() > deadline or draining()
