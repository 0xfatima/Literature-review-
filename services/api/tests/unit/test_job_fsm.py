# stdlib-only
from datetime import datetime, timedelta, timezone

from app.jobs import fsm
from tests.helpers import raises

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
POLICY = fsm.RecoveryPolicy(timedelta(seconds=120), timedelta(seconds=60))


def test_legal_and_illegal_transitions():
    assert fsm.can_transition("queued", "running") and fsm.can_transition("running", "succeeded")
    assert fsm.can_transition("running", "queued")  # recovery path
    for term in fsm.TERMINAL_STATUSES:
        assert not any(fsm.can_transition(term, s) for s in fsm.ALL_STATUSES)  # terminal states are final
    assert not fsm.can_transition("queued", "succeeded")  # cannot skip running
    with raises(fsm.InvalidTransition):
        fsm.assert_transition("succeeded", "running")


def test_fingerprint_ignores_key_order_and_changes_with_content():
    a = fsm.fingerprint("t", "p1", {"x": 1, "y": [1, 2]})
    assert a == fsm.fingerprint("t", "p1", {"y": [1, 2], "x": 1})
    assert a != fsm.fingerprint("t", "p1", {"x": 2, "y": [1, 2]})
    assert a != fsm.fingerprint("t", "p2", {"x": 1, "y": [1, 2]})
    assert a != fsm.fingerprint("other", "p1", {"x": 1, "y": [1, 2]})
    assert fsm.fingerprint("t", None, {}) != fsm.fingerprint("t", "p1", {})


def test_only_queued_and_running_are_covered_by_the_in_flight_uniqueness_index():
    # Legitimate reruns: once a job reaches ANY terminal state it must not block an identical new job.
    assert set(fsm.ACTIVE_STATUSES) == {"queued", "running"}
    for s in fsm.TERMINAL_STATUSES + (fsm.AWAITING_INPUT,):
        assert s not in fsm.ACTIVE_STATUSES


def test_stale_running_detection():
    assert fsm.is_stale_running("running", NOW - timedelta(seconds=121), None, NOW, POLICY)
    assert not fsm.is_stale_running("running", NOW - timedelta(seconds=119), None, NOW, POLICY)
    assert fsm.is_stale_running("running", None, NOW - timedelta(seconds=500), NOW, POLICY)  # no heartbeat ever
    assert fsm.is_stale_running("running", None, None, NOW, POLICY)
    assert not fsm.is_stale_running("succeeded", NOW - timedelta(days=1), None, NOW, POLICY)


def test_queued_redispatch_detection():
    assert fsm.needs_redispatch("queued", None, NOW - timedelta(seconds=61), NOW, POLICY)  # never sent
    assert not fsm.needs_redispatch("queued", NOW - timedelta(seconds=10), NOW - timedelta(days=1), NOW, POLICY)
    assert not fsm.needs_redispatch("running", None, NOW - timedelta(days=1), NOW, POLICY)


def test_worker_loss_retries_then_fails_visibly():
    assert fsm.worker_loss_outcome(1, 3) == "queued" and fsm.worker_loss_outcome(2, 3) == "queued"
    assert fsm.worker_loss_outcome(3, 3) == "failed"  # bounded: never loops forever


def test_job_type_registry_marks_cancellability():
    assert fsm.JOB_TYPES["system.ping"]["cancellable"] is True
