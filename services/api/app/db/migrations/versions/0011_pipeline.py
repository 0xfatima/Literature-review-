"""Phase 13: controlled-autonomy pipeline runs, their append-only events, and stage invalidations.

Revision ID: 0011_pipeline
Revises: 0010_report_drafts
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0011_pipeline"
down_revision = "0010_report_drafts"
branch_labels = None
depends_on = None

EVENT_GUARD = """
CREATE FUNCTION pipeline_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM pipeline_runs WHERE id = OLD.run_id) THEN
    RETURN OLD;  -- cascaded removal (project deleted)
  END IF;
  RAISE EXCEPTION 'pipeline_events are append-only'
    USING ERRCODE = 'restrict_violation';
END $$;
"""


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def _json(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("thread_id", False),
        sa.Column("graph_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(32)),
        sa.Column("start_stage", sa.String(32), nullable=False, server_default="protocol"),
        _json("autonomy"),
        sa.Column("pending", pg.JSONB),
        _json("outputs"),
        _json("counters"),
        _uuid("protocol_version_id"),
        _uuid("supersedes_id"),
        sa.Column("rerun_reason", sa.Text),
        sa.Column("error_summary", sa.Text),
        _uuid("created_by"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        _now("created_at"),
        _now("updated_at"),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_pipeline_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_pipeline_runs_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            name="fk_pipeline_runs_protocol_version_id_protocol_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["pipeline_runs.id"], name="fk_pipeline_runs_supersedes_id_pipeline_runs", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_pipeline_runs_created_by_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status in ('queued','running','waiting','paused','completed','cancelled','failed','superseded')",
            name="status",
        ),
    )
    op.create_index("ix_pipeline_runs_project_created", "pipeline_runs", ["project_id", "created_at"])
    op.create_index(
        "uq_pipeline_runs_one_active",
        "pipeline_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status in ('queued','running','waiting','paused')"),
    )

    op.create_table(
        "pipeline_events",
        _uuid("id", False),
        _uuid("run_id", False),
        _uuid("project_id", False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32)),
        _json("payload"),
        _uuid("actor_id"),
        _now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_pipeline_events"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["pipeline_runs.id"], name="fk_pipeline_events_run_id_pipeline_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_pipeline_events_project_id_projects", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_pipeline_events_run_created", "pipeline_events", ["run_id", "created_at"])
    op.execute(EVENT_GUARD)
    op.execute(
        "CREATE TRIGGER pipeline_events_append_only BEFORE UPDATE OR DELETE ON pipeline_events "
        "FOR EACH ROW EXECUTE FUNCTION pipeline_event_guard()"
    )

    op.create_table(
        "stage_invalidations",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("pipeline_run_id"),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("cause", sa.String(32), nullable=False),
        _json("detail"),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text),
        _uuid("created_by"),
        _now("created_at"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_stage_invalidations"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_stage_invalidations_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name="fk_stage_invalidations_pipeline_run_id_pipeline_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("status in ('open','resolved','dismissed')", name="status"),
    )
    op.create_index("ix_stage_invalidations_project_status", "stage_invalidations", ["project_id", "status"])


def downgrade() -> None:
    op.drop_table("stage_invalidations")
    op.execute("DROP TRIGGER IF EXISTS pipeline_events_append_only ON pipeline_events")
    op.execute("DROP FUNCTION IF EXISTS pipeline_event_guard()")
    op.drop_table("pipeline_events")
    op.drop_table("pipeline_runs")
