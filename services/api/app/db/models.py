"""ORM models: users, auth_sessions, projects, audit_events, jobs (Phase 1); conversations, messages (Phase 2);
protocol_versions, protocol_questions, protocol_criteria, workflow_runs, workflow_events (Phase 3).

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
            "kind in ('started','question','answer','stage','error','needs_attention','recovered','revision')",
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
