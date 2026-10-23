"""Typed workflow state (spec §5.3). IDs and compact metadata only: protocol text, papers and documents live in
tables. Values are plain JSON types so the checkpoint is readable and stable across library upgrades."""

from __future__ import annotations

from typing import TypedDict


class ReviewState(TypedDict, total=False):
    project_id: str
    run_id: str
    thread_id: str
    protocol_version_id: str
    stage: str
    step: int  # number of questions answered on this thread; part of every question id
    iteration_count: int
    max_iterations: int
    framework: str | None  # routing hint only (skip the elements step); the table is authoritative
    return_to: str | None  # after editing one section from the review, go back to the review
    mode: str | None  # "start" | "revise" | "rebuild": how START routes
    stop_reason: str | None
    correlation_id: str | None
