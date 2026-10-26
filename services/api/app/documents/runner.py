"""Supervises child processes that handle untrusted input (the PDF parser, the OCR engine).

* hard wall-clock timeout, then the whole process group is killed;
* output size cap (a child that prints without end is killed);
* a MINIMAL environment: the child never sees database URLs, API keys or session secrets;
* a periodic `on_tick` callback so the job heartbeat stays fresh during long runs (the sweeper presumes a job whose
  heartbeat is older than JOB_STALE_AFTER_S has lost its worker and re-queues it, which would duplicate the work).
Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PDF_CHILD = Path(__file__).with_name("pdf_child.py")


@dataclass
class ChildResult:
    returncode: int | None
    stdout: bytes
    stderr_tail: str
    timed_out: bool
    output_truncated: bool

    @property
    def crashed(self) -> bool:
        return self.returncode is None or self.returncode != 0


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    env.update(extra or {})
    return env


def run_child(
    argv: Sequence[str],
    *,
    timeout_s: float,
    max_output_bytes: int,
    on_tick: Callable[[], None] | None = None,
    tick_s: float = 5.0,
    env_extra: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> ChildResult:
    proc = subprocess.Popen(  # noqa: S603  argv is built by this application from validated values, never a shell
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env(env_extra),
        cwd=cwd,
        start_new_session=True,
        close_fds=True,
    )
    out = bytearray()
    err = bytearray()
    truncated = threading.Event()

    def pump(stream: object, sink: bytearray, cap: int, flag: threading.Event | None) -> None:
        read = stream.read1 if hasattr(stream, "read1") else stream.read  # type: ignore[attr-defined]
        while chunk := read(65536):
            sink += chunk
            if len(sink) > cap:
                if flag is not None:
                    flag.set()
                    _kill(proc)
                del sink[cap:]
                break
        with contextlib.suppress(Exception):
            stream.close()  # type: ignore[attr-defined]

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, out, max_output_bytes, truncated), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err, 16384, None), daemon=True),
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout_s
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            _kill(proc)
            break
        try:
            proc.wait(timeout=min(tick_s, remaining))
            break
        except subprocess.TimeoutExpired:
            if on_tick is not None:
                on_tick()
    proc.wait()
    for t in threads:
        t.join(timeout=5)
    rc = proc.returncode
    return ChildResult(
        returncode=None if (timed_out or truncated.is_set()) else rc,
        stdout=bytes(out),
        stderr_tail=bytes(err[-2000:]).decode("utf-8", "replace"),
        timed_out=timed_out,
        output_truncated=truncated.is_set(),
    )


def _kill(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


def pdf_child_argv(*args: str) -> list[str]:
    """`python -I` ignores PYTHON* variables and the user site, so the child cannot be steered by the environment."""
    return [sys.executable, "-I", str(PDF_CHILD), *args]
