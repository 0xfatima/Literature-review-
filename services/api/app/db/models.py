"""ORM models for Phase 1: users, auth_sessions, projects, audit_events, jobs.

Password-reset tokens are intentionally absent (recovery is deferred; see ADR amendment A8).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
                        text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, now_col, uuid_pk

PROJECT_STATUSES = ("setup", "searching", "screening", "awaiting_pdfs", "extracting",
                    "synthesizing", "draft_ready", "needs_attention", "completed")
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
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = now_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    user: Mapped[User] = relationship()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
                                                nullable=False)
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
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
                                                nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                         ForeignKey("projects.id", ondelete="CASCADE"))
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
    rerun_of_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                              ForeignKey("jobs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = now_col()
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status in " + JOB_STATUS_SQL, name="status"),
        CheckConstraint("progress between 0 and 100", name="progress"),
        # (1) retried HTTP submissions return the same job
        Index("uq_jobs_owner_request_key", "owner_id", "request_key", unique=True,
              postgresql_where=text("request_key IS NOT NULL")),
        # (2) identical work may not be IN FLIGHT twice; finished jobs never block a rerun
        Index("uq_jobs_active_fingerprint", "owner_id", "type",
              text(f"COALESCE(project_id, '{ZERO_UUID}'::uuid)"), "input_fingerprint", unique=True,
              postgresql_where=text("status IN ('queued','running')")),
        Index("ix_jobs_status_heartbeat", "status", "heartbeat_at"),
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
    )
