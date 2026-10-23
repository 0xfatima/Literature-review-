"""Phase 3: versioned review protocol (versions, questions, criteria), the review workflow run and its event log,
and the schema that holds LangGraph checkpoints.

Approved protocol versions are immutable *in the database*, not only in the service: triggers reject every change
to an approved/superseded version except approved -> superseded, and every insert/update/delete of its questions
or criteria. Deleting the whole project still works (the guards stand aside once the project row is gone).

Revision ID: 0003_protocol_workflow
Revises: 0002_conversations
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003_protocol_workflow"
down_revision = "0002_conversations"
branch_labels = None
depends_on = None

CHECKPOINT_SCHEMA = "lg_checkpoints"


def _now() -> sa.TextClause:
    return sa.text("now()")


def _jsonb_list() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


VERSION_GUARD = """
CREATE FUNCTION protocol_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Project deletion cascades through here; once the project row is gone nothing is being "changed".
  IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  IF OLD.status = 'draft' THEN
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'approved' AND NEW.status = 'superseded'
     AND (to_jsonb(NEW) - 'status' - 'superseded_at') = (to_jsonb(OLD) - 'status' - 'superseded_at') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'protocol version % is %: approved protocol versions are immutable', OLD.version, OLD.status
    USING ERRCODE = 'restrict_violation';
END $$;
"""

CHILD_GUARD = """
CREATE FUNCTION protocol_child_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT status INTO st FROM protocol_versions WHERE id = OLD.protocol_version_id;
    IF st IS NOT NULL AND st <> 'draft' THEN
      RAISE EXCEPTION 'cannot change % of a % protocol version', TG_TABLE_NAME, st USING ERRCODE = 'restrict_violation';
    END IF;
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    SELECT status INTO st FROM protocol_versions WHERE id = NEW.protocol_version_id;
    IF st IS NOT NULL AND st <> 'draft' THEN
      RAISE EXCEPTION 'cannot change % of a % protocol version', TG_TABLE_NAME, st USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
  END IF;
  RETURN OLD;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "protocol_versions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("supersedes_id", pg.UUID(as_uuid=True)),
        sa.Column("objective", sa.Text),
        sa.Column("framework", sa.String(16)),
        sa.Column("framework_source", sa.String(16)),
        sa.Column("framework_rationale", sa.Text),
        sa.Column("framework_elements", pg.JSONB, nullable=False, server_default=_jsonb_list()),
        sa.Column("date_from", sa.Integer),
        sa.Column("date_to", sa.Integer),
        sa.Column("languages", pg.JSONB, nullable=False, server_default=_jsonb_list()),
        sa.Column("study_designs", pg.JSONB, nullable=False, server_default=_jsonb_list()),
        sa.Column("sources", pg.JSONB, nullable=False, server_default=_jsonb_list()),
        sa.Column("search_strategy", sa.Text),
        sa.Column("extraction_plan", sa.Text),
        sa.Column("synthesis_plan", sa.Text),
        sa.Column("change_reason", sa.Text),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("approved_by", pg.UUID(as_uuid=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("impact", pg.JSONB),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_protocol_versions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_protocol_versions_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["protocol_versions.id"], name="fk_protocol_versions_supersedes_id_protocol_versions"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_protocol_versions_created_by_users"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], name="fk_protocol_versions_approved_by_users"),
        sa.UniqueConstraint("project_id", "version", name="uq_protocol_versions_project_id"),
        sa.CheckConstraint("status in ('draft','approved','superseded')", name=op.f("ck_protocol_versions_status")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_protocol_versions_version")),
        sa.CheckConstraint("version = 1 OR change_reason IS NOT NULL", name=op.f("ck_protocol_versions_change_reason")),
        sa.CheckConstraint(
            "status = 'draft' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL AND content_hash IS NOT NULL)",
            name=op.f("ck_protocol_versions_approval"),
        ),
        sa.CheckConstraint(
            "date_from IS NULL OR date_to IS NULL OR date_from <= date_to", name=op.f("ck_protocol_versions_date_range")
        ),
    )
    op.create_index(
        "uq_protocol_versions_one_draft",
        "protocol_versions",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_protocol_versions_one_approved",
        "protocol_versions",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )

    op.create_table(
        "protocol_questions",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol_version_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_protocol_questions"),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            name="fk_protocol_questions_protocol_version_id_protocol_versions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("protocol_version_id", "stable_key", name="uq_protocol_questions_version_key"),
    )
    op.create_table(
        "protocol_criteria",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol_version_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_protocol_criteria"),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            name="fk_protocol_criteria_protocol_version_id_protocol_versions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("protocol_version_id", "stable_key", name="uq_protocol_criteria_version_key"),
        sa.CheckConstraint("kind in ('inclusion','exclusion')", name=op.f("ck_protocol_criteria_kind")),
        sa.CheckConstraint("origin in ('user','suggested')", name=op.f("ck_protocol_criteria_origin")),
        sa.CheckConstraint("state in ('proposed','accepted','rejected')", name=op.f("ck_protocol_criteria_state")),
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", pg.UUID(as_uuid=True)),
        sa.Column("thread_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_name", sa.String(64), nullable=False),
        sa.Column("graph_version", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("pending_question_id", pg.UUID(as_uuid=True)),
        sa.Column("pending_question", pg.JSONB),
        sa.Column("protocol_version_id", pg.UUID(as_uuid=True)),
        sa.Column("iteration_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_iterations", sa.Integer, nullable=False),
        sa.Column("stop_reason", sa.String(64)),
        sa.Column("error_summary", sa.Text),
        sa.Column("stage_before_revision", sa.String(32)),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_workflow_runs_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_workflow_runs_conversation_id_conversations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            name="fk_workflow_runs_protocol_version_id_protocol_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_workflow_runs_created_by_users", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("project_id", name="uq_workflow_runs_project_id"),
        sa.CheckConstraint(
            "status in ('queued','running','awaiting_input','idle','failed','needs_attention')",
            name=op.f("ck_workflow_runs_status"),
        ),
        sa.CheckConstraint(
            "stage in ('DRAFT','PROTOCOL_REVIEW','SEARCH','RECORD_SCREENING','FULL_TEXT_SCREENING','EXTRACTION',"
            "'SYNTHESIS','REPORT')",
            name=op.f("ck_workflow_runs_stage"),
        ),
        sa.CheckConstraint(
            "(status = 'awaiting_input') = (pending_question_id IS NOT NULL AND pending_question IS NOT NULL)",
            name=op.f("ck_workflow_runs_pending_question"),
        ),
        sa.CheckConstraint("iteration_count >= 0 AND max_iterations >= 1", name=op.f("ck_workflow_runs_iterations")),
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("question_id", pg.UUID(as_uuid=True)),
        sa.Column("stage", sa.String(32)),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_events"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["workflow_runs.id"], name="fk_workflow_events_run_id_workflow_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_workflow_events_actor_id_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "kind in ('started','question','answer','stage','error','needs_attention','recovered','revision')",
            name=op.f("ck_workflow_events_kind"),
        ),
    )
    op.create_index("ix_workflow_events_run_created", "workflow_events", ["run_id", "created_at"])
    op.create_index(
        "uq_workflow_events_run_kind_question",
        "workflow_events",
        ["run_id", "kind", "question_id"],
        unique=True,
        postgresql_where=sa.text("question_id IS NOT NULL"),
    )

    op.add_column("jobs", sa.Column("workflow_run_id", pg.UUID(as_uuid=True)))
    op.create_index("ix_jobs_workflow_run_id", "jobs", ["workflow_run_id"])
    op.create_foreign_key(
        "fk_jobs_workflow_run_id_workflow_runs", "jobs", "workflow_runs", ["workflow_run_id"], ["id"], ondelete="CASCADE"
    )

    op.execute(VERSION_GUARD)
    op.execute(
        "CREATE TRIGGER protocol_versions_immutable BEFORE UPDATE OR DELETE ON protocol_versions "
        "FOR EACH ROW EXECUTE FUNCTION protocol_version_guard()"
    )
    op.execute(CHILD_GUARD)
    for table in ("protocol_questions", "protocol_criteria"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION protocol_child_guard()"
        )

    # LangGraph's checkpointer creates and migrates its own tables in this schema on first use
    # (app/workflows/runtime.py, under an advisory lock). Keeping them out of `public` keeps Alembic's model
    # comparison clean and makes the checkpoint store easy to identify in backups.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")


def downgrade() -> None:
    op.execute(f"DROP SCHEMA IF EXISTS {CHECKPOINT_SCHEMA} CASCADE")
    for table in ("protocol_questions", "protocol_criteria"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP TRIGGER IF EXISTS protocol_versions_immutable ON protocol_versions")
    op.execute("DROP FUNCTION IF EXISTS protocol_child_guard()")
    op.execute("DROP FUNCTION IF EXISTS protocol_version_guard()")
    op.drop_constraint("fk_jobs_workflow_run_id_workflow_runs", "jobs", type_="foreignkey")
    op.drop_index("ix_jobs_workflow_run_id", table_name="jobs")
    op.drop_column("jobs", "workflow_run_id")
    op.drop_table("workflow_events")
    op.drop_table("workflow_runs")
    op.drop_table("protocol_criteria")
    op.drop_table("protocol_questions")
    op.drop_table("protocol_versions")
