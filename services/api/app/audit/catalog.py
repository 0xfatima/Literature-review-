"""What the audit trail must contain and how events are presented (Phase 14). Stdlib only.

REQUIRED maps each "significant event" named in the requirements (spec Phase 14) to the action names that record it.
tests/unit/test_audit_catalog.py fails if any required action is no longer emitted anywhere in the application code,
so removing an audit call cannot go unnoticed.
"""

from __future__ import annotations

REQUIRED: dict[str, tuple[str, ...]] = {
    "searches": ("search.started", "search.finished", "search.retried"),
    "uploads": ("document.uploaded", "document.removed", "document.downloaded"),
    "protocol approvals": ("protocol.approved",),
    "criteria changes": ("protocol.draft_updated", "protocol.revision_started"),
    "screening decisions": ("screening.decided", "screening.bulk_decided", "screening.undone"),
    "full-text decisions": ("full_text_screening.decided",),
    "extraction edits": ("evidence.edited", "evidence.approved", "extraction_schema.approved"),
    "appraisal judgements": ("appraisal.edited", "appraisal.approved"),
    "synthesis approvals": ("synthesis.accepted",),
    "exports": ("report.exported", "evidence.exported", "audit.exported"),
    "account security": ("auth.login_succeeded", "auth.login_failed", "auth.logout"),
    "AI data consent": ("llm_consent.granted", "llm_consent.revoked"),
    "autonomy": ("pipeline.started", "pipeline.answered", "pipeline.cancelled"),
}

# First matching prefix wins; order from specific to general.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("auth.", "account"),
    ("user.", "account"),
    ("llm_consent.", "ai"),
    ("relevance.", "ai"),
    ("assistant.", "ai"),
    ("project.", "project"),
    ("conversation.", "project"),
    ("protocol.", "protocol"),
    ("workflow.", "protocol"),
    ("search.", "search"),
    ("screening.", "screening"),
    ("full_text_screening.", "screening"),
    ("full_text.", "screening"),
    ("study.", "screening"),
    ("document.", "documents"),
    ("extraction_schema.", "extraction"),
    ("evidence.", "extraction"),
    ("appraisal.", "appraisal"),
    ("synthesis.", "synthesis"),
    ("report.", "report"),
    ("pipeline.", "pipeline"),
    ("job.", "jobs"),
    ("audit.", "audit"),
)
CATEGORY_NAMES = tuple(dict.fromkeys(c for _, c in CATEGORIES)) + ("other",)

LABELS: dict[str, str] = {
    "auth.login_failed": "Failed sign-in",
    "auth.login_succeeded": "Signed in",
    "auth.logout": "Signed out",
    "auth.password_reset_by_operator": "Password reset by operator",
    "user.registered": "Account created",
    "protocol.approved": "Protocol approved",
    "protocol.revision_started": "Protocol revision started",
    "protocol.draft_updated": "Protocol draft edited",
    "search.started": "Search started",
    "search.finished": "Search finished",
    "screening.decided": "Screening decision",
    "screening.bulk_decided": "Bulk screening decision",
    "screening.undone": "Screening decision undone",
    "full_text_screening.decided": "Full-text decision",
    "full_text.not_retrieved": "Full text marked as not retrieved",
    "full_text.retrieval_restored": "Full text marked as sought again",
    "study.ungrouped": "Study grouping removed",
    "document.uploaded": "File uploaded",
    "document.purged": "File purged (retention)",
    "evidence.edited": "Extracted value edited",
    "evidence.approved": "Extracted value approved",
    "synthesis.accepted": "Synthesis accepted",
    "report.exported": "Report exported",
    "evidence.exported": "Evidence table exported",
    "audit.exported": "Audit log exported",
}


def category_of(action: str) -> str:
    return next((c for prefix, c in CATEGORIES if action.startswith(prefix)), "other")


def prefixes_for(category: str) -> list[str]:
    return [p for p, c in CATEGORIES if c == category]


def label_of(action: str) -> str:
    if action in LABELS:
        return LABELS[action]
    words = action.replace(".", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else action


def required_actions() -> set[str]:
    return {a for actions in REQUIRED.values() for a in actions}
