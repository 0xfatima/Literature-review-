"""Phase 1 baseline: users, auth_sessions, projects, audit_events (append-only), jobs.

Revision ID: 0001_baseline
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

ZERO = "00000000-0000-0000-0000-000000000000"


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("status in ('active','disabled')", name=op.f("ck_users_status")),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent", sa.String(200)),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "projects",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("topic_original", sa.Text, nullable=False),
        sa.Column("discipline", sa.String(100)),
        sa.Column("review_type", sa.String(100)),
        sa.Column("status", sa.String(24), nullable=False, server_default="setup"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_projects_owner_id_users", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status in ('setup','searching','screening','awaiting_pdfs','extracting','synthesizing',"
            "'draft_ready','needs_attention','completed')", name=op.f("ck_projects_status")),
    )
    op.create_index("ix_projects_owner_created", "projects", ["owner_id", "created_at"])
    op.create_table(
        "audit_events",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True)),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True)),
        sa.Column("before", pg.JSONB),
        sa.Column("after", pg.JSONB),
        sa.Column("reason", sa.Text),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_project_at", "audit_events", ["project_id", "at"])
    op.create_index("ix_audit_events_actor_at", "audit_events", ["actor_id", "at"])
    # Append-only enforcement at the database level (applies to every role, including the app role).
    op.execute("""
        CREATE FUNCTION audit_events_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP USING ERRCODE = '42501';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_block_mutation();
    """)
    op.execute("""
        CREATE TRIGGER audit_events_no_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION audit_events_block_mutation();
    """)
    op.create_table(
        "jobs",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True)),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("progress_message", sa.String(200)),
        sa.Column("request_key", sa.String(128)),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("input", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", pg.JSONB),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_summary", sa.Text),
        sa.Column("rerun_of_job_id", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("enqueued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_jobs_owner_id_users", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_jobs_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rerun_of_job_id"], ["jobs.id"], name="fk_jobs_rerun_of_job_id_jobs", ondelete="SET NULL"),
        sa.CheckConstraint("status in ('queued','running','succeeded','failed','cancelled','awaiting_input')",
                           name=op.f("ck_jobs_status")),
        sa.CheckConstraint("progress between 0 and 100", name=op.f("ck_jobs_progress")),
    )
    # (1) a retried HTTP submission (same Idempotency-Key) returns the same job
    op.execute("CREATE UNIQUE INDEX uq_jobs_owner_request_key ON jobs (owner_id, request_key) "
               "WHERE request_key IS NOT NULL")
    # (2) identical work cannot be in flight twice; finished jobs never block legitimate reruns
    op.execute(f"CREATE UNIQUE INDEX uq_jobs_active_fingerprint ON jobs "
               f"(owner_id, type, COALESCE(project_id, '{ZERO}'::uuid), input_fingerprint) "
               f"WHERE status IN ('queued','running')")
    op.create_index("ix_jobs_status_heartbeat", "jobs", ["status", "heartbeat_at"])
    op.create_index("ix_jobs_owner_created", "jobs", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_block_mutation()")
    op.drop_table("projects")
    op.drop_table("auth_sessions")
    op.drop_table("users")
