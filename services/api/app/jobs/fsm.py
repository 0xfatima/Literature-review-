"""Job state machine, fingerprinting and recovery rules. Stdlib only (pure functions).

IDEMPOTENCY DESIGN (replaces the Phase-0 permanent unique(project_id, type, idempotency_key)):

Three distinct concerns, three distinct mechanisms, none of which blocks a *legitimate* rerun:

1. Duplicate HTTP submissions (double-click, client retry after timeout)
   -> optional `request_key` (Idempotency-Key header), unique per (owner_id, request_key).
      Replaying the same key returns the SAME job. A new key = a new, intentional request.
2. Identical work already in flight (two tabs start the same search)
   -> `input_fingerprint`, unique among ACTIVE jobs only (status in queued|running).
      Once the job is succeeded/failed/cancelled the constraint no longer applies, so reruns
      (same query next week, retry after failure, rerun after protocol change) are allowed.
3. At-least-once broker delivery / worker crash
   -> compare-and-set claim (`queued -> running` only if still queued) + data-level natural keys
      in later phases. A redelivered message for a finished job is a no-op.
Reruns can record lineage via `rerun_of_job_id`; they never need to defeat a constraint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, AWAITING_INPUT = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "awaiting_input",
)
ALL_STATUSES = (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, AWAITING_INPUT)
ACTIVE_STATUSES = (QUEUED, RUNNING)  # statuses covered by the in-flight uniqueness index
TERMINAL_STATUSES = (SUCCEEDED, FAILED, CANCELLED)

TRANSITIONS: dict[str, frozenset[str]] = {
    QUEUED: frozenset({RUNNING, CANCELLED, FAILED}),
    RUNNING: frozenset({SUCCEEDED, FAILED, CANCELLED, QUEUED, AWAITING_INPUT}),  # RUNNING->QUEUED = recovery
    AWAITING_INPUT: frozenset({QUEUED, CANCELLED, FAILED}),
    SUCCEEDED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

# Registry of job types. Only safe-to-cancel types may be cancelled (spec: "cancellation where safe").
JOB_TYPES: dict[str, dict[str, Any]] = {
    "system.ping": {"cancellable": True, "max_attempts": 3},
    # Moves the review workflow one human step forward. Not cancellable: a half-applied step is resolved by
    # resuming from the checkpoint, never by abandoning it mid-way.
    "workflow.advance": {"cancellable": False, "max_attempts": 3},
}


class InvalidTransition(Exception):
    pass


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, frozenset())


def assert_transition(src: str, dst: str) -> None:
    if not can_transition(src, dst):
        raise InvalidTransition(f"{src} -> {dst} is not allowed")


def fingerprint(job_type: str, project_id: str | None, payload: dict[str, Any]) -> str:
    """Canonical SHA-256 over (type, project, input). Key order and whitespace do not matter."""
    canonical = json.dumps(
        {"t": job_type, "p": project_id, "i": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecoveryPolicy:
    stale_after: timedelta  # running job with no heartbeat for this long is presumed lost
    requeue_queued_after: timedelta  # queued job whose message may never have been sent (crash after commit)


def is_stale_running(
    status: str, heartbeat_at: datetime | None, started_at: datetime | None, now: datetime, policy: RecoveryPolicy
) -> bool:
    if status != RUNNING:
        return False
    last = heartbeat_at or started_at
    return last is None or (now - last) > policy.stale_after


def needs_redispatch(
    status: str, enqueued_at: datetime | None, created_at: datetime, now: datetime, policy: RecoveryPolicy
) -> bool:
    if status != QUEUED:
        return False
    ref = enqueued_at or created_at
    return (now - ref) > policy.requeue_queued_after


def worker_loss_outcome(attempts: int, max_attempts: int) -> str:
    """After a lost worker: retry while attempts remain, else fail visibly (never silently loop)."""
    return QUEUED if attempts < max_attempts else FAILED
