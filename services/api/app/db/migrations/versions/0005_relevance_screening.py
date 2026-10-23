"""Phase 5: model_runs (every AI call), relevance_assessments (relevance, separate from eligibility),
screening_decisions (append-only human decisions; trigger-guarded) and screening_states (current decision pointer).

Revision ID: 0005_relevance_screening
Revises: 0004_scholarly_search
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005_relevance_screening"
down_revision = "0004_scholarly_search"
branch_labels = None
depends_on = None

DECISION_GUARD = """
CREATE FUNCTION screening_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Deleting the project cascades through here; once the project row is gone nothing is being "changed".
  IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'screening decisions are append-only: record a new decision instead'
    USING ERRCODE = 'restrict_violation';
END $$;
"""


def _now() -> sa.TextClause:
    return sa.text("now()")


def _list() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _obj() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "model_runs",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("job_id"),
        sa.Column("purpose", sa.String(48), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(48), nullable=False),
        sa.Column("data_class", sa.String(24), nullable=False),
        sa.Column("settings", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_kind", sa.String(32)),
        sa.Column("error_summary", sa.Text),
        sa.Column("item_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_model_runs"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_model_runs_project_id_projects", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_model_runs_job_id_jobs", ondelete="SET NULL"),
        sa.CheckConstraint("status in ('succeeded','failed')", name=op.f("ck_model_runs_status")),
        sa.CheckConstraint(
            "data_class in ('public_metadata','private_document')", name=op.f("ck_model_runs_data_class")
        ),
    )
    op.create_index("ix_model_runs_project_created", "model_runs", ["project_id", "created_at"])

    op.create_table(
        "relevance_assessments",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id", False),
        _uuid("protocol_version_id", False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("method_version", sa.String(48), nullable=False),
        _uuid("model_run_id"),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False, server_default="normal"),
        sa.Column("explanation", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("eligibility_suggestion", sa.String(16)),
        sa.Column("suggestion_detail", pg.JSONB),
        sa.Column("warnings", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_relevance_assessments"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_relevance_assessments_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_relevance_assessments_paper_id_paper_records",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"], ["protocol_versions.id"],
            name="fk_relevance_assessments_protocol_version_id_protocol_versions",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"], ["model_runs.id"], name="fk_relevance_assessments_model_run_id_model_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "paper_id", "protocol_version_id", "method", "method_version", name="uq_relevance_assessments_paper_id"
        ),
        sa.CheckConstraint("method in ('lexical','llm')", name=op.f("ck_relevance_assessments_method")),
        sa.CheckConstraint(
            "band in ('high','moderate_high','possible','low','insufficient','not_assessed')",
            name=op.f("ck_relevance_assessments_band"),
        ),
        sa.CheckConstraint("score between 0 and 100", name=op.f("ck_relevance_assessments_score")),
        sa.CheckConstraint("confidence in ('normal','low')", name=op.f("ck_relevance_assessments_confidence")),
        sa.CheckConstraint(
            "eligibility_suggestion IS NULL OR eligibility_suggestion in ('include','exclude','uncertain')",
            name=op.f("ck_relevance_assessments_suggestion"),
        ),
        sa.CheckConstraint(
            "method = 'llm' OR eligibility_suggestion IS NULL", name=op.f("ck_relevance_assessments_suggestion_method")
        ),
    )
    op.create_index(
        "ix_relevance_assessments_project_pv", "relevance_assessments", ["project_id", "protocol_version_id"]
    )

    op.create_table(
        "screening_decisions",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id", False),
        _uuid("protocol_version_id", False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(80)),
        sa.Column("reason_label", sa.Text),
        sa.Column("note", sa.Text),
        _uuid("reviewer_id"),
        sa.Column("action", sa.String(8), nullable=False),
        _uuid("batch_id"),
        _uuid("supersedes_id"),
        sa.Column("assessment_ids", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_screening_decisions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_screening_decisions_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_screening_decisions_paper_id_paper_records",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"], ["protocol_versions.id"],
            name="fk_screening_decisions_protocol_version_id_protocol_versions",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name="fk_screening_decisions_reviewer_id_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["screening_decisions.id"], name="fk_screening_decisions_supersedes_id_screening_decisions"
        ),
        sa.CheckConstraint("stage in ('title_abstract')", name=op.f("ck_screening_decisions_stage")),
        sa.CheckConstraint(
            "decision in ('include','exclude','uncertain','cleared')", name=op.f("ck_screening_decisions_decision")
        ),
        sa.CheckConstraint("action in ('decide','bulk','undo')", name=op.f("ck_screening_decisions_action")),
        sa.CheckConstraint(
            "decision = 'exclude' OR reason_code IS NULL", name=op.f("ck_screening_decisions_reason_only_for_exclude")
        ),
    )
    op.create_index("ix_screening_decisions_paper_created", "screening_decisions", ["paper_id", "created_at"])
    op.create_index("ix_screening_decisions_project_batch", "screening_decisions", ["project_id", "batch_id"])

    op.create_table(
        "screening_states",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id", False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        _uuid("decision_id", False),
        _uuid("protocol_version_id", False),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_screening_states"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_screening_states_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_screening_states_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["screening_decisions.id"], name="fk_screening_states_decision_id_screening_decisions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"], ["protocol_versions.id"],
            name="fk_screening_states_protocol_version_id_protocol_versions",
        ),
        sa.UniqueConstraint("paper_id", "stage", name="uq_screening_states_paper_id"),
        sa.CheckConstraint("stage in ('title_abstract')", name=op.f("ck_screening_states_stage")),
        sa.CheckConstraint(
            "decision in ('include','exclude','uncertain','cleared')", name=op.f("ck_screening_states_decision")
        ),
    )
    op.create_index("ix_screening_states_project_stage", "screening_states", ["project_id", "stage", "decision"])

    op.execute(DECISION_GUARD)
    op.execute(
        "CREATE TRIGGER screening_decisions_append_only BEFORE UPDATE OR DELETE ON screening_decisions "
        "FOR EACH ROW EXECUTE FUNCTION screening_decision_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS screening_decisions_append_only ON screening_decisions")
    op.execute("DROP FUNCTION IF EXISTS screening_decision_guard()")
    op.drop_table("screening_states")
    op.drop_table("screening_decisions")
    op.drop_table("relevance_assessments")
    op.drop_table("model_runs")
