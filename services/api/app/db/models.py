"""ORM models: users, auth_sessions, projects, audit_events, jobs (Phase 1); conversations, messages (Phase 2);
protocol_versions, protocol_questions, protocol_criteria, workflow_runs, workflow_events (Phase 3);
search_runs, search_queries, paper_records, paper_source_records (Phase 4);
model_runs, relevance_assessments, screening_decisions, screening_states (Phase 5).

Password-reset tokens are intentionally absent (recovery is deferred; see ADR amendment A8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, now_col, uuid_pk

PROJECT_STATUSES = (
    "setup",
    "searching",
    "screening",
    "awaiting_pdfs",
    "extracting",
    "synthesizing",
    "draft_ready",
    "needs_attention",
    "completed",
)
JOB_STATUS_SQL = "('queued','running','succeeded','failed','cancelled','awaiting_input')"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)  # stored lower-cased
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    __table_args__ = (CheckConstraint("status in ('active','disabled')", name="status"),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = now_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    user: Mapped[User] = relationship()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic_original: Mapped[str] = mapped_column(Text, nullable=False)  # immutable: spec §3.2 preserves original text
    discipline: Mapped[str | None] = mapped_column(String(100))
    review_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="setup")
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    __table_args__ = (
        CheckConstraint("status in " + str(PROJECT_STATUSES).replace('"', "'"), name="status"),
        Index("ix_projects_owner_created", "owner_id", "created_at"),
    )


class AuditEvent(Base):
    """Append-only (DB trigger blocks UPDATE/DELETE). No FKs on purpose: events must survive
    deletion of the project/user they describe. Free-text user content is never stored here."""

    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = now_col()
    __table_args__ = (
        Index("ix_audit_events_project_at", "project_id", "at"),
        Index("ix_audit_events_actor_at", "actor_id", "at"),
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    progress_message: Mapped[str | None] = mapped_column(String(200))
    request_key: Mapped[str | None] = mapped_column(String(128))
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error_summary: Mapped[str | None] = mapped_column(Text)
    rerun_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = now_col()
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status in " + JOB_STATUS_SQL, name="status"),
        CheckConstraint("progress between 0 and 100", name="progress"),
        # (1) retried HTTP submissions return the same job
        Index(
            "uq_jobs_owner_request_key",
            "owner_id",
            "request_key",
            unique=True,
            postgresql_where=text("request_key IS NOT NULL"),
        ),
        # (2) identical work may not be IN FLIGHT twice; finished jobs never block a rerun
        Index(
            "uq_jobs_active_fingerprint",
            "owner_id",
            "type",
            text(f"COALESCE(project_id, '{ZERO_UUID}'::uuid)"),
            "input_fingerprint",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_jobs_status_heartbeat", "status", "heartbeat_at"),
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
    )


MESSAGE_ROLES = ("user", "assistant", "system")


class Conversation(Base):
    """A chat thread inside one project. Ownership is inherited from the project (no owner column to drift)."""

    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # 'auto' titles are replaced by the first message; a title the user typed ('user') is never overwritten.
    title_source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="auto")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("title_source in ('auto','user')", name="title_source"),
        CheckConstraint("message_count >= 0", name="message_count"),
        Index("ix_conversations_project_activity", "project_id", "updated_at"),
    )


class Message(Base):
    """One chat message. `seq` gives a gap-free, deterministic order per conversation (timestamps can tie).
    `payload` is reserved for structured content (approval cards, citations) added by later phases."""

    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_id"),
        CheckConstraint("role in ('user','assistant','system')", name="role"),
        CheckConstraint("seq >= 1", name="seq"),
        Index(
            "uq_messages_conversation_client_id",
            "conversation_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
    )


PROTOCOL_STATUSES = ("draft", "approved", "superseded")


class ProtocolVersion(Base):
    """One version of a project's review protocol. Exactly one draft and one approved version may exist per
    project at a time. Approved and superseded rows are immutable: a database trigger rejects any change except
    approved -> superseded, and rejects changes to their questions/criteria (migration 0003)."""

    __tablename__ = "protocol_versions"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    # NO ACTION (not SET NULL) on these references: a SET NULL would be an UPDATE of an immutable approved row.
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("protocol_versions.id"))
    objective: Mapped[str | None] = mapped_column(Text)
    framework: Mapped[str | None] = mapped_column(String(16))
    framework_source: Mapped[str | None] = mapped_column(String(16))
    framework_rationale: Mapped[str | None] = mapped_column(Text)  # the advisor's explanation, kept as shown
    framework_elements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    date_from: Mapped[int | None] = mapped_column(Integer)
    date_to: Mapped[int | None] = mapped_column(Integer)
    languages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    study_designs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    sources: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    search_strategy: Mapped[str | None] = mapped_column(Text)
    extraction_plan: Mapped[str | None] = mapped_column(Text)
    synthesis_plan: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str | None] = mapped_column(Text)  # required for every version after the first
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256 of the canonical approved content
    impact: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # diff + affected stages vs the previous approval
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    questions: Mapped[list[ProtocolQuestion]] = relationship(
        order_by="ProtocolQuestion.position", cascade="all, delete-orphan", passive_deletes=True
    )
    criteria: Mapped[list[ProtocolCriterion]] = relationship(
        order_by="ProtocolCriterion.position", cascade="all, delete-orphan", passive_deletes=True
    )
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_protocol_versions_project_id"),
        CheckConstraint("status in ('draft','approved','superseded')", name="status"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint("version = 1 OR change_reason IS NOT NULL", name="change_reason"),
        CheckConstraint(
            "status = 'draft' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND content_hash IS NOT NULL)",
            name="approval",
        ),
        CheckConstraint("date_from IS NULL OR date_to IS NULL OR date_from <= date_to", name="date_range"),
        Index("uq_protocol_versions_one_draft", "project_id", unique=True, postgresql_where=text("status = 'draft'")),
        Index(
            "uq_protocol_versions_one_approved", "project_id", unique=True, postgresql_where=text("status = 'approved'")
        ),
    )


class ProtocolQuestion(Base):
    __tablename__ = "protocol_questions"
    id: Mapped[uuid.UUID] = uuid_pk()
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)  # same across versions
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("protocol_version_id", "stable_key", name="uq_protocol_questions_version_key"),)


class ProtocolCriterion(Base):
    __tablename__ = "protocol_criteria"
    id: Mapped[uuid.UUID] = uuid_pk()
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (
        UniqueConstraint("protocol_version_id", "stable_key", name="uq_protocol_criteria_version_key"),
        CheckConstraint("kind in ('inclusion','exclusion')", name="kind"),
        CheckConstraint("origin in ('user','suggested')", name="origin"),
        CheckConstraint("state in ('proposed','accepted','rejected')", name="state"),
    )


WORKFLOW_STATUSES = ("queued", "running", "awaiting_input", "idle", "failed", "needs_attention")
WORKFLOW_STATUS_SQL = "('queued','running','awaiting_input','idle','failed','needs_attention')"
WORKFLOW_STAGE_SQL = (
    "('DRAFT','PROTOCOL_REVIEW','SEARCH','RECORD_SCREENING','FULL_TEXT_SCREENING','EXTRACTION','SYNTHESIS','REPORT')"
)


class WorkflowRun(Base):
    """The review workflow of one project (one run per project). `thread_id` is the LangGraph thread whose
    checkpoints live in schema lg_checkpoints; typed tables stay authoritative (ADR D3: if they disagree with the
    checkpoint, the run is marked needs_attention and the tables win)."""

    __tablename__ = "workflow_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    graph_name: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DRAFT")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    pending_question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    pending_question: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    protocol_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id", ondelete="SET NULL")
    )
    iteration_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    stage_before_revision: Mapped[str | None] = mapped_column(String(32))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    __table_args__ = (
        CheckConstraint("status in " + WORKFLOW_STATUS_SQL, name="status"),
        CheckConstraint("stage in " + WORKFLOW_STAGE_SQL, name="stage"),
        CheckConstraint(
            "(status = 'awaiting_input') = (pending_question_id IS NOT NULL AND pending_question IS NOT NULL)",
            name="pending_question",
        ),
        CheckConstraint("iteration_count >= 0 AND max_iterations >= 1", name="iterations"),
    )


class WorkflowEvent(Base):
    """Append-only log of one run: questions asked, answers received, stage changes, failures, recoveries.
    An answer is recorded at most once per question (unique index), which makes a duplicate resume a no-op.
    Unlike audit_events this log holds the user's answers (it is project data, deleted with the project)."""

    __tablename__ = "workflow_events"
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    question_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (
        CheckConstraint(
            "kind in ('started','question','answer','stage','error','needs_attention','recovered','revision','search')",
            name="kind",
        ),
        Index("ix_workflow_events_run_created", "run_id", "created_at"),
        Index(
            "uq_workflow_events_run_kind_question",
            "run_id",
            "kind",
            "question_id",
            unique=True,
            postgresql_where=text("question_id IS NOT NULL"),
        ),
    )


# ---------- Phase 4: scholarly search ----------

SEARCH_TIERS = ("open_access", "additional")
SEARCH_RUN_STATUSES = ("queued", "running", "completed", "partial", "failed", "cancelled")
SEARCH_RUN_ACTIVE = ("queued", "running")
SEARCH_QUERY_STATUSES = ("pending", "running", "completed", "capped", "partial", "failed", "cancelled")
EXPRESSION_SOURCES = ("search_strategy", "framework_elements", "objective", "topic", "user_edited")
BROADEN_DECISIONS = ("yes", "no", "later")
MATCH_BASES = ("doi", "pmid", "arxiv_id", "openalex_id", "title_author_year", "new")
OA_STATUSES = ("open", "closed", "unknown")
ACCESS_STATUSES = ("open_pdf_link", "open_landing_page", "metadata_only")
FULL_TEXT_STATUSES = ("not_obtained", "uploaded", "parsed", "failed")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class SearchRun(Base):
    """One search the user started: an expression run against one or more providers, bound to the approved protocol
    version it was built from. Runs are never overwritten; a rerun is a new row (spec 3.3 "preserve prior runs").
    Status `completed` only when EVERY provider query completed; anything less is `partial` or `failed`."""

    __tablename__ = "search_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # NO ACTION: approved protocol versions are immutable and are only removed with the whole project.
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    expression_source: Mapped[str] = mapped_column(String(24), nullable=False)
    derived_expression: Mapped[str | None] = mapped_column(Text)  # what the protocol produced (kept if edited)
    derivation_notes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    providers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    protocol_sources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # {listed, matched, unsupported, added_beyond_protocol}
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    records_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    papers_new: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    papers_matched_existing: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duplicates_in_run: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    possible_duplicates: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    broaden_decision: Mapped[str | None] = mapped_column(String(8))
    broaden_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broaden_decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queries: Mapped[list[SearchQuery]] = relationship(
        order_by="SearchQuery.position", cascade="all, delete-orphan", passive_deletes=True
    )
    __table_args__ = (
        CheckConstraint("tier in " + _in(SEARCH_TIERS), name="tier"),
        CheckConstraint("status in " + _in(SEARCH_RUN_STATUSES), name="status"),
        CheckConstraint("expression_source in " + _in(EXPRESSION_SOURCES), name="expression_source"),
        CheckConstraint("broaden_decision IS NULL OR broaden_decision in " + _in(BROADEN_DECISIONS), name="broaden"),
        CheckConstraint(
            "records_retrieved >= 0 AND records_skipped >= 0 AND papers_new >= 0 AND papers_matched_existing >= 0 "
            "AND duplicates_in_run >= 0 AND possible_duplicates >= 0",
            name="counts",
        ),
        Index("ix_search_runs_project_created", "project_id", "created_at"),
        # One search at a time per project: deduplication is serial by design (no racing merges).
        Index(
            "uq_search_runs_one_active",
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )


class SearchQuery(Base):
    """The exact query sent to ONE provider within a run, with its own status, cursor and counts. The cursor and
    the per-page commit make a retried or redelivered job resume where it stopped, without duplicate records."""

    __tablename__ = "search_queries"
    id: Mapped[uuid.UUID] = uuid_pk()
    search_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_runs.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(300), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    filters_applied: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    cursor: Mapped[str | None] = mapped_column(Text)
    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_reported: Mapped[int | None] = mapped_column(Integer)
    records_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    records_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # last page received
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("search_run_id", "provider", name="uq_search_queries_search_run_id"),
        CheckConstraint("status in " + _in(SEARCH_QUERY_STATUSES), name="status"),
        CheckConstraint("pages_fetched >= 0 AND records_retrieved >= 0 AND records_skipped >= 0", name="counts"),
        Index("ix_search_queries_project", "project_id"),
    )


class PaperRecord(Base):
    """One deduplicated paper in a project (the canonical record). Fields are filled from the first source that
    supplied them; `field_sources` says which provider supplied each field. Identifiers are unique per project.
    Access fields are what providers REPORT; `full_text_status` is what this application actually holds."""

    __tablename__ = "paper_records"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_key: Mapped[str] = mapped_column(String(500), nullable=False)
    authors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    first_author_key: Mapped[str | None] = mapped_column(String(120))
    abstract: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(String(500))
    publication_type: Mapped[str | None] = mapped_column(String(64))
    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    language: Mapped[str | None] = mapped_column(String(8))
    doi: Mapped[str | None] = mapped_column(String(255))
    pmid: Mapped[str | None] = mapped_column(String(16))
    pmcid: Mapped[str | None] = mapped_column(String(16))
    arxiv_id: Mapped[str | None] = mapped_column(String(64))
    openalex_id: Mapped[str | None] = mapped_column(String(32))
    other_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    is_preprint: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    has_preprint_version: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_retracted: Mapped[bool | None] = mapped_column(Boolean)
    oa_status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="unknown")
    oa_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(200))
    access_status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="metadata_only")
    full_text_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="not_obtained")
    field_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    providers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    possible_duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_records.id", ondelete="SET NULL")
    )
    possible_duplicate_reason: Mapped[str | None] = mapped_column(Text)
    first_seen_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_runs.id", ondelete="SET NULL")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = now_col()
    updated_at: Mapped[datetime] = now_col()
    __table_args__ = (
        CheckConstraint("oa_status in " + _in(OA_STATUSES), name="oa_status"),
        CheckConstraint("access_status in " + _in(ACCESS_STATUSES), name="access_status"),
        CheckConstraint("full_text_status in " + _in(FULL_TEXT_STATUSES), name="full_text_status"),
        CheckConstraint("source_count >= 0", name="source_count"),
        Index(
            "uq_paper_records_project_doi", "project_id", "doi", unique=True, postgresql_where=text("doi IS NOT NULL")
        ),
        Index(
            "uq_paper_records_project_pmid",
            "project_id",
            "pmid",
            unique=True,
            postgresql_where=text("pmid IS NOT NULL"),
        ),
        Index(
            "uq_paper_records_project_arxiv",
            "project_id",
            "arxiv_id",
            unique=True,
            postgresql_where=text("arxiv_id IS NOT NULL"),
        ),
        Index(
            "uq_paper_records_project_openalex",
            "project_id",
            "openalex_id",
            unique=True,
            postgresql_where=text("openalex_id IS NOT NULL"),
        ),
        Index("ix_paper_records_project_title_key", "project_id", "title_key"),
        Index("ix_paper_records_project_created", "project_id", "created_at"),
    )


class PaperSourceRecord(Base):
    """One record exactly as one provider returned it in one query: the provenance of a paper. `raw` is the
    provider's original record; `normalized` is what this application derived from it. Unique per query and
    provider record id, so a resumed page never inserts the same record twice."""

    __tablename__ = "paper_source_records"
    id: Mapped[uuid.UUID] = uuid_pk()
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_records.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    search_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_runs.id", ondelete="CASCADE"), nullable=False
    )
    search_query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normalized: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    match_basis: Mapped[str] = mapped_column(String(24), nullable=False)
    retrieved_at: Mapped[datetime] = now_col()
    __table_args__ = (
        UniqueConstraint("search_query_id", "provider_record_id", name="uq_paper_source_records_search_query_id"),
        CheckConstraint("match_basis in " + _in(MATCH_BASES), name="match_basis"),
        CheckConstraint("rank >= 1", name="rank"),
        Index("ix_paper_source_records_paper", "paper_id"),
        Index("ix_paper_source_records_run", "search_run_id"),
        Index("ix_paper_source_records_project", "project_id"),
    )


# ---------- Phase 5: relevance and screening ----------

MODEL_RUN_STATUSES = ("succeeded", "failed")
RELEVANCE_METHODS = ("lexical", "llm")
RELEVANCE_BANDS = ("high", "moderate_high", "possible", "low", "insufficient", "not_assessed")
SCREENING_STAGES = ("title_abstract",)
SCREENING_DECISIONS = ("include", "exclude", "uncertain", "cleared")
SCREENING_ACTIONS = ("decide", "bulk", "undo")


class ModelRun(Base):
    """One call to an AI model (spec 5.x): provider, model, prompt version, settings, tokens, duration, status and the
    class of data sent. Written for failures as well as successes. Never contains the API key or the prompt text."""

    __tablename__ = "model_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(48), nullable=False)
    data_class: Mapped[str] = mapped_column(String(24), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_summary: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (
        CheckConstraint("status in " + _in(MODEL_RUN_STATUSES), name="status"),
        CheckConstraint("data_class in ('public_metadata','private_document')", name="data_class"),
        Index("ix_model_runs_project_created", "project_id", "created_at"),
    )


class RelevanceAssessment(Base):
    """A relevance assessment of one paper for one approved protocol version by one method version. Relevance is
    NOT eligibility. An `llm` row may carry an eligibility SUGGESTION; it is never a decision. Re-running the same
    method version replaces the row (the model run history keeps every call)."""

    __tablename__ = "relevance_assessments"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_records.id", ondelete="CASCADE"), nullable=False
    )
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    method_version: Mapped[str] = mapped_column(String(48), nullable=False)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_runs.id", ondelete="SET NULL")
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False, server_default="normal")
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    eligibility_suggestion: Mapped[str | None] = mapped_column(String(16))
    suggestion_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (
        UniqueConstraint(
            "paper_id", "protocol_version_id", "method", "method_version", name="uq_relevance_assessments_paper_id"
        ),
        CheckConstraint("method in " + _in(RELEVANCE_METHODS), name="method"),
        CheckConstraint("band in " + _in(RELEVANCE_BANDS), name="band"),
        CheckConstraint("score between 0 and 100", name="score"),
        CheckConstraint("confidence in ('normal','low')", name="confidence"),
        CheckConstraint(
            "eligibility_suggestion IS NULL OR eligibility_suggestion in ('include','exclude','uncertain')",
            name="suggestion",
        ),
        CheckConstraint("method = 'llm' OR eligibility_suggestion IS NULL", name="suggestion_method"),
        Index("ix_relevance_assessments_project_pv", "project_id", "protocol_version_id"),
    )


class ScreeningDecision(Base):
    """Append-only record of a HUMAN screening decision (a database trigger rejects UPDATE/DELETE while the project
    exists). A revision or an undo is a new row that `supersedes_id` the previous current decision; the history is
    never rewritten. `assessment_ids` records which AI/lexical assessments were on screen when the human decided."""

    __tablename__ = "screening_decisions"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_records.id", ondelete="CASCADE"), nullable=False
    )
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    reason_label: Mapped[str | None] = mapped_column(Text)  # snapshot of the reason text as shown
    note: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("screening_decisions.id"))
    assessment_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = now_col()
    __table_args__ = (
        CheckConstraint("stage in " + _in(SCREENING_STAGES), name="stage"),
        CheckConstraint("decision in " + _in(SCREENING_DECISIONS), name="decision"),
        CheckConstraint("action in " + _in(SCREENING_ACTIONS), name="action"),
        CheckConstraint("decision = 'exclude' OR reason_code IS NULL", name="reason_only_for_exclude"),
        Index("ix_screening_decisions_paper_created", "paper_id", "created_at"),
        Index("ix_screening_decisions_project_batch", "project_id", "batch_id"),
    )


class ScreeningState(Base):
    """The CURRENT screening decision of a paper at a stage (a pointer into the append-only log), with optimistic
    concurrency. Derived data: it can always be rebuilt from screening_decisions."""

    __tablename__ = "screening_states"
    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_records.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("screening_decisions.id", ondelete="CASCADE"), nullable=False
    )
    protocol_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("protocol_versions.id"), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_at: Mapped[datetime] = now_col()
    __table_args__ = (
        UniqueConstraint("paper_id", "stage", name="uq_screening_states_paper_id"),
        CheckConstraint("stage in " + _in(SCREENING_STAGES), name="stage"),
        CheckConstraint("decision in " + _in(SCREENING_DECISIONS), name="decision"),
        Index("ix_screening_states_project_stage", "project_id", "stage", "decision"),
    )
