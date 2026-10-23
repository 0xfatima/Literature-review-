"""The human questions the review workflow asks. Stdlib only.

A question is a small JSON-able dict; its id is DETERMINISTIC (uuid5 of thread, step and node). LangGraph re-runs a
node from the top when it is resumed, so a node must build the identical question every time it runs: that is
what makes "resume with this question id" well defined and lets the chat message for a question be written once.
"""

from __future__ import annotations

import uuid
from typing import Any

NS = uuid.UUID("6c6f6d71-2d70-726f-746f-636f6c2d7633")  # fixed namespace for question ids

# node -> (kind, stage, prompt, help). Order here is the default order of the protocol conversation.
PROTOCOL_STEPS: dict[str, tuple[str, str, str]] = {
    "objective": (
        "DRAFT",
        "What is the objective of this review? Your original topic is kept unchanged; the objective is your "
        "statement of what the review should establish.",
        "One or two sentences. You can skip this for now and come back to it.",
    ),
    "framework": (
        "DRAFT",
        "Which question framework should structure the review?",
        "A framework breaks the question into parts that drive the search and the eligibility criteria.",
    ),
    "elements": (
        "DRAFT",
        "Describe each part of the framework.",
        "Fill in what you know. Empty parts are allowed and can be completed later.",
    ),
    "questions": (
        "DRAFT",
        "What review questions should the review answer?",
        "Add one or more questions. A draft question built from your framework answers is offered if possible.",
    ),
    "scope": (
        "DRAFT",
        "What is the scope: publication years, languages, study designs and sources to search?",
        "All optional. Leaving something empty means it is not restricted.",
    ),
    "criteria": (
        "DRAFT",
        "Which inclusion and exclusion criteria apply?",
        "Suggestions restate your own answers; accept, edit or reject each one, and add your own.",
    ),
    "review": (
        "PROTOCOL_REVIEW",
        "Review the protocol summary. Approving it fixes this version for searching and screening.",
        "Approval is recorded with your name and the time. Later changes create a new version with a reason, and "
        "show which results may need to be rerun. You can also go back and edit any section, or leave and return.",
    ),
}
ORDER = tuple(PROTOCOL_STEPS)
EDITABLE_SECTIONS = ORDER[:-1]


def question_id(thread_id: str, step: int, node: str) -> str:
    return str(uuid.uuid5(NS, f"{thread_id}:{step}:{node}"))


def build(node: str, *, thread_id: str, step: int, protocol_version_id: str) -> dict[str, Any]:
    stage, prompt, help_text = PROTOCOL_STEPS[node]
    return {
        "id": question_id(thread_id, step, node),
        "kind": node,
        "step": step,
        "stage": stage,
        "prompt": prompt,
        "help": help_text,
        "protocol_version_id": protocol_version_id,
        "actions": ["approve", "edit"] if node == "review" else ["save", "skip"],
    }


def default_next(node: str, *, framework: str | None) -> str:
    """Next node in the default order; the elements step is skipped when there is no framework to fill in."""
    nxt = ORDER[ORDER.index(node) + 1]
    if nxt == "elements" and framework in (None, "none"):
        return "questions"
    return nxt
