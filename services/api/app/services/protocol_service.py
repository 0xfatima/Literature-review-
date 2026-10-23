"""Review protocol: drafts, approval and versioned revisions. The ONLY writer of protocol tables.

Rules enforced here (and, for immutability, again by database triggers):
  * at most one draft and one approved version per project;
  * a draft is edited with optimistic concurrency (`expected_row_version`);
  * approval requires a complete protocol (app.protocol.content.approval_issues), records approver, time and a
    SHA-256 of the exact content, and supersedes the previously approved version;
  * an approved version is never edited: a change starts a NEW draft that copies it and must give a reason;
  * approving a revision stores the diff and the downstream stages it affects (`impact`).

Audit events carry field NAMES and ids only, never protocol text (audit rule from Phase 1). The text itself, the
reason for a change and the full diff live on the version rows, which are deleted with the project.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models import Project, ProtocolCriterion, ProtocolQuestion, ProtocolVersion, User
from app.protocol import content as pc
from app.protocol import stages
from app.repositories import protocols as repo
from app.services import audit_service

EDITABLE_FIELDS = (
    "objective",
    "framework",
    "framework_source",
    "framework_elements",
    "questions",
    "criteria",
    "date_from",
    "date_to",
    "languages",
    "study_designs",
    "sources",
    "search_strategy",
    "extraction_plan",
    "synthesis_plan",
)
MIN_REASON, MAX_REASON = 3, 2000


# ---------- conversion ----------


def to_content(pv: ProtocolVersion) -> dict[str, Any]:
    c = pc.empty()
    for f in ("objective", "framework", "framework_source", "date_from", "date_to", "search_strategy"):
        c[f] = getattr(pv, f)
    c["extraction_plan"], c["synthesis_plan"] = pv.extraction_plan, pv.synthesis_plan
    c["framework_elements"] = list(pv.framework_elements or [])
    c["languages"], c["study_designs"], c["sources"] = list(pv.languages), list(pv.study_designs), list(pv.sources)
    c["questions"] = [{"stable_key": str(q.stable_key), "text": q.text} for q in pv.questions]
    c["criteria"] = [
        {
            "stable_key": str(x.stable_key),
            "kind": x.kind,
            "text": x.text,
            "rationale": x.rationale,
            "origin": x.origin,
            "state": x.state,
        }
        for x in pv.criteria
    ]
    return c


def _write(db: Session, pv: ProtocolVersion, c: dict[str, Any]) -> None:
    for f in (
        "objective",
        "framework",
        "framework_source",
        "date_from",
        "date_to",
        "search_strategy",
        "extraction_plan",
        "synthesis_plan",
    ):
        setattr(pv, f, c[f])
    pv.framework_elements, pv.languages = c["framework_elements"], c["languages"]
    pv.study_designs, pv.sources = c["study_designs"], c["sources"]
    # Children are replaced wholesale on a draft (the trigger forbids this on approved versions). Delete first and
    # flush: SQLAlchemy would otherwise INSERT the new rows before deleting the old ones, and an unchanged item
    # (same stable_key) would collide with itself on the unique (version, stable_key) constraint.
    pv.questions.clear()
    pv.criteria.clear()
    db.flush()
    pv.questions = [
        ProtocolQuestion(stable_key=uuid.UUID(q["stable_key"]), position=i, text=q["text"])
        for i, q in enumerate(c["questions"])
    ]
    pv.criteria = [
        ProtocolCriterion(
            stable_key=uuid.UUID(x["stable_key"]),
            position=i,
            kind=x["kind"],
            text=x["text"],
            rationale=x["rationale"],
            origin=x["origin"],
            state=x["state"],
        )
        for i, x in enumerate(c["criteria"])
    ]
    db.flush()


def _with_keys(items: Any) -> Any:
    """New questions/criteria arrive without a stable_key; give each a fresh one (existing keys are kept)."""
    if not isinstance(items, list):
        return items
    return [
        ({**it, "stable_key": it.get("stable_key") or str(uuid.uuid4())} if isinstance(it, dict) else it)
        for it in items
    ]


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        return pc.normalize(raw)
    except pc.ContentError as e:
        raise ValidationFailed(str(e), details=e.details) from None


# ---------- reads ----------


def require_writable(project: Project) -> None:
    if project.archived_at is not None:
        raise Conflict("This project is archived. Unarchive it to change the protocol.", code="project_archived")


def draft(db: Session, project: Project, *, lock: bool = False) -> ProtocolVersion | None:
    return repo.by_status(db, project.id, "draft", lock=lock)


def approved(db: Session, project: Project) -> ProtocolVersion | None:
    return repo.by_status(db, project.id, "approved")


def versions(db: Session, project: Project) -> list[ProtocolVersion]:
    return repo.list_for_project(db, project.id)


def get_version(db: Session, project: Project, version_id: uuid.UUID) -> ProtocolVersion:
    pv = repo.get_in_project(db, project.id, version_id)
    if pv is None:
        raise NotFound("Protocol version not found.")
    return pv


def view(pv: ProtocolVersion) -> dict[str, Any]:
    c = to_content(pv)
    issues = pc.approval_issues(c) if pv.status == "draft" else []
    return {
        "id": pv.id,
        "version": pv.version,
        "status": pv.status,
        "row_version": pv.row_version,
        "content": c,
        "framework_rationale": pv.framework_rationale,
        "issues": [i.as_dict() for i in issues],
        "can_approve": pv.status == "draft" and not pc.blocking(issues),
        "summary": pc.summary_lines(c),
        "change_reason": pv.change_reason,
        "supersedes_id": pv.supersedes_id,
        "created_at": pv.created_at,
        "updated_at": pv.updated_at,
        "approved_at": pv.approved_at,
        "approved_by": pv.approved_by,
        "content_hash": pv.content_hash,
        "impact": pv.impact,
        "superseded_at": pv.superseded_at,
    }


# ---------- writes (callers commit) ----------


def create_first_draft(db: Session, user: User, project: Project) -> ProtocolVersion:
    """Version 1, empty. Idempotent: returns the existing draft if there is one."""
    existing = draft(db, project)
    if existing is not None:
        return existing
    if approved(db, project) is not None:
        raise Conflict("The protocol is approved. Start a revision to change it.", code="protocol_approved")
    pv = ProtocolVersion(project_id=project.id, version=repo.next_version(db, project.id), created_by=user.id)
    db.add(pv)
    db.flush()
    audit_service.record(
        db,
        action="protocol.draft_created",
        entity_type="protocol_version",
        entity_id=pv.id,
        actor_id=user.id,
        project_id=project.id,
        after={"version": pv.version},
    )
    return pv


def update_draft(
    db: Session,
    user: User,
    project: Project,
    *,
    expected_row_version: int,
    patch: dict[str, Any],
    framework_rationale: str | None = None,
) -> ProtocolVersion:
    """Apply a partial update (only the keys present) to the draft. Lists are replaced, not merged."""
    require_writable(project)
    unknown = set(patch) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationFailed("Unknown protocol fields.", details=[{"field": f, "issue": "unknown"} for f in unknown])
    pv = draft(db, project, lock=True)
    if pv is None:
        raise Conflict("There is no draft to edit. Start a revision first.", code="no_draft")
    if pv.row_version != expected_row_version:
        raise Conflict("The protocol was changed elsewhere. Reload and try again.", code="version_conflict")
    before = to_content(pv)
    raw = {**before, **patch}
    raw["questions"], raw["criteria"] = _with_keys(raw["questions"]), _with_keys(raw["criteria"])
    if "framework" in patch and "framework_elements" not in patch and patch["framework"] != before["framework"]:
        raw["framework_elements"] = []  # elements of another framework do not carry over
    new = _normalize(raw)
    changed = [f for f in EDITABLE_FIELDS if before.get(f) != new.get(f)]
    if not changed and framework_rationale is None:
        return pv
    _write(db, pv, new)
    if "framework" in changed or framework_rationale is not None:
        pv.framework_rationale = framework_rationale
    pv.row_version += 1
    pv.updated_at = datetime.now(UTC)
    audit_service.record(
        db,
        action="protocol.draft_updated",
        entity_type="protocol_version",
        entity_id=pv.id,
        actor_id=user.id,
        project_id=project.id,
        after={"version": pv.version, "changed_fields": changed},
    )
    return pv


def approve(
    db: Session, user: User, project: Project, *, version_id: uuid.UUID, expected_row_version: int
) -> ProtocolVersion:
    """Approve exactly the draft the user saw (id + row_version). Supersedes the previous approval."""
    require_writable(project)
    pv = draft(db, project, lock=True)
    if pv is None or pv.id != version_id:
        raise Conflict("This protocol version is not the current draft.", code="not_current_draft")
    if pv.row_version != expected_row_version:
        raise Conflict(
            "The protocol changed after you reviewed it. Review the latest version before approving.",
            code="version_conflict",
        )
    c = _normalize(to_content(pv))
    problems = pc.blocking(pc.approval_issues(c))
    if problems:
        raise ValidationFailed(
            "The protocol is not complete enough to approve.",
            details=[{"field": i.field, "issue": i.message} for i in problems],
        )
    previous = approved(db, project)
    now = datetime.now(UTC)
    if previous is not None:
        changes = pc.diff(to_content(previous), c)
        pv.impact = {"compared_with_version": previous.version, "changes": changes, **stages.impact(changes)}
        previous.status, previous.superseded_at = "superseded", now
        db.flush()  # free the one-approved-per-project slot before taking it
    pv.status, pv.approved_by, pv.approved_at = "approved", user.id, now
    pv.content_hash = pc.content_hash(c)
    db.flush()
    audit_service.record(
        db,
        action="protocol.approved",
        entity_type="protocol_version",
        entity_id=pv.id,
        actor_id=user.id,
        project_id=project.id,
        after={
            "version": pv.version,
            "content_hash": pv.content_hash,
            "supersedes_version": previous.version if previous else None,
            "affected_stages": (pv.impact or {}).get("affected_stages", []),
        },
    )
    return pv


def start_revision(db: Session, user: User, project: Project, *, reason: str) -> ProtocolVersion:
    """New draft copying the approved version. The reason is mandatory and stored on the new version."""
    require_writable(project)
    why = " ".join(reason.split())
    if not (MIN_REASON <= len(why) <= MAX_REASON):
        raise ValidationFailed(
            f"Give a reason for the change ({MIN_REASON}-{MAX_REASON} characters).",
            details=[{"field": "reason", "issue": "length"}],
        )
    if draft(db, project) is not None:
        raise Conflict("A revision is already in progress.", code="draft_exists")
    base = approved(db, project)
    if base is None:
        raise Conflict("Only an approved protocol can be revised.", code="not_approved")
    pv = ProtocolVersion(
        project_id=project.id,
        version=repo.next_version(db, project.id),
        supersedes_id=base.id,
        change_reason=why,
        created_by=user.id,
        framework_rationale=base.framework_rationale,
    )
    db.add(pv)
    db.flush()
    _write(db, pv, to_content(base))
    audit_service.record(
        db,
        action="protocol.revision_started",
        entity_type="protocol_version",
        entity_id=pv.id,
        actor_id=user.id,
        project_id=project.id,
        after={"version": pv.version, "revises_version": base.version},
    )
    return pv
