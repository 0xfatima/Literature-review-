"""Controlled-autonomy policy: stage order, autonomy defaults, technical retry/backoff and budgets. Stdlib only.

Technical retries (a job failed for a transient reason) and research reruns (an upstream decision changed) are kept
apart: retries re-dispatch the SAME operation with the same inputs and are counted per stage; reruns are new
pipeline generations with a recorded reason, which invalidate downstream outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

STAGES = ("protocol", "search", "screening", "extraction", "appraisal", "synthesis", "report")
STAGE_LABELS = {
    "protocol": "Protocol planning",
    "search": "Search",
    "screening": "Screening support",
    "extraction": "Extraction",
    "appraisal": "Appraisal",
    "synthesis": "Synthesis",
    "report": "Report",
}
# What the pipeline may start without asking first. Human judgements (protocol approval, screening decisions,
# reviewing AI values, approving appraisals, accepting the synthesis, verifying the report) are always interrupts.
DEFAULT_AUTONOMY: dict[str, bool] = {
    "search": False,  # the search plan is confirmed by a person before it runs
    "screening": True,  # word-overlap relevance ranking (advisory; never a decision)
    "extraction": False,  # sends document text to an AI provider: ask first (consent gates also apply)
    "appraisal": False,
    "synthesis": True,  # rule-based draft; AI statements only if `synthesis_ai` is also allowed
    "synthesis_ai": False,
    "report": True,  # deterministic draft from stored data
}
# Job failure kinds worth retrying automatically; anything else goes to a person.
TRANSIENT_MARKERS = ("worker lost", "timeout", "timed out", "rate limit", "temporarily", "unavailable", "connection")


def downstream(stage: str) -> list[str]:
    """Stages after `stage` (their outputs depend on it)."""
    return list(STAGES[STAGES.index(stage) + 1 :])


def normalise_autonomy(raw: dict[str, Any] | None) -> dict[str, bool]:
    out = dict(DEFAULT_AUTONOMY)
    for k, v in (raw or {}).items():
        if k in out:
            out[k] = bool(v)
    return out


def is_transient(error: str | None) -> bool:
    e = (error or "").lower()
    return any(m in e for m in TRANSIENT_MARKERS)


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    attempt: int
    not_before: datetime | None
    reason: str


def retry_decision(
    attempts_so_far: int, error: str | None, *, max_retries: int, base_s: float, cap_s: float, now: datetime
) -> RetryDecision:
    """Bounded exponential backoff for transient failures; non-transient failures or exhausted retries go to a
    person (who may retry manually, skip the stage or stop)."""
    if not is_transient(error):
        return RetryDecision(False, attempts_so_far, None, "The failure does not look transient; a person decides.")
    if attempts_so_far >= max_retries:
        return RetryDecision(False, attempts_so_far, None, f"Gave up after {attempts_so_far} automatic retries.")
    delay = min(cap_s, base_s * (2**attempts_so_far))
    return RetryDecision(True, attempts_so_far + 1, now + timedelta(seconds=delay), f"Retrying in {int(delay)} s.")


def over_budget(used_tokens: int, token_budget: int, jobs_dispatched: int, max_jobs: int) -> str | None:
    if token_budget and used_tokens >= token_budget:
        return f"AI token budget for this run reached ({used_tokens} of {token_budget})."
    if max_jobs and jobs_dispatched >= max_jobs:
        return f"Background job limit for this run reached ({jobs_dispatched} of {max_jobs})."
    return None


def diff_outputs(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """Plain sentences describing what changed in a stage's output summary after a rerun."""
    before = before or {}
    lines = []
    for k, v in after.items():
        old = before.get(k)
        if old != v:
            label = k.replace("_", " ")
            lines.append(f"{label}: {old if old is not None else '—'} → {v}")
    return lines
