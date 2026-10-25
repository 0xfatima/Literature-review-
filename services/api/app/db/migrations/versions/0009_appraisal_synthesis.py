"""Phase 10: critical appraisal (appraisal_records + append-only appraisal_revisions) and versioned narrative
synthesis (synthesis_versions); evidence_outputs.kind gains 'synthesis'.

Revision ID: 0009_appraisal_synthesis
Revises: 0008_evidence_extraction
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009_appraisal_synthesis"
down_revision = "0008_evidence_extraction"
branch_labels = None
depends_on = None

REVISION_GUARD = """
CREATE FUNCTION appraisal_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM appraisal_records WHERE id = OLD.appraisal_record_id) THEN
    RETURN OLD;  -- cascaded removal of the record (project or paper deleted)
  END IF;
  RAISE EXCEPTION 'appraisal_revisions are append-only'
    USING ERRCODE = 'restrict_violation';
END $$;
"""


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def _now() -> sa.TextClause:
    return sa.text("now()")


def _obj() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _ts(name: str, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    if nullable:
        return sa.Column(name, sa.DateTime(timezone=True))
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=_now())


def upgrade() -> None:
    op.execute("ALTER TABLE evidence_outputs DROP CONSTRAINT ck_evidence_outputs_kind")
    op.execute("ALTER TABLE evidence_outputs ADD CONSTRAINT ck_evidence_outputs_kind CHECK (kind in ('csv_export','synthesis'))")

    op.create_table(
        "appraisal_records",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id", False),
        sa.Column("framework_key", sa.String(40), nullable=False),
        sa.Column("framework_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("answers", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("suggested_overall", sa.String(24)),
        sa.Column("overall_judgement", sa.String(24)),
        sa.Column("overall_justification", sa.Text),
        _uuid("reviewer_id"),
        _ts("approved_at", nullable=True),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_appraisal_records"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_appraisal_records_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_appraisal_records_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name="fk_appraisal_records_reviewer_id_users", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("paper_id", "framework_key", name="uq_appraisal_records_paper_framework"),
        sa.CheckConstraint("status in ('draft','approved')", name="status"),
    )
    op.create_index("ix_appraisal_records_project", "appraisal_records", ["project_id"])

    op.create_table(
        "appraisal_revisions",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("appraisal_record_id", False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("before", pg.JSONB),
        sa.Column("after", pg.JSONB, nullable=False),
        _uuid("actor_id"),
        _uuid("model_run_id"),
        sa.Column("reason", sa.Text),
        _ts("at"),
        sa.PrimaryKeyConstraint("id", name="pk_appraisal_revisions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_appraisal_revisions_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["appraisal_record_id"],
            ["appraisal_records.id"],
            name="fk_appraisal_revisions_appraisal_record_id_appraisal_records",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"], ["model_runs.id"], name="fk_appraisal_revisions_model_run_id_model_runs", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("appraisal_record_id", "revision", name="uq_appraisal_revisions_record_revision"),
    )
    op.create_index("ix_appraisal_revisions_project", "appraisal_revisions", ["project_id"])
    op.execute(REVISION_GUARD)
    op.execute(
        "CREATE TRIGGER appraisal_revisions_append_only BEFORE UPDATE OR DELETE ON appraisal_revisions "
        "FOR EACH ROW EXECUTE FUNCTION appraisal_revision_guard()"
    )

    op.create_table(
        "synthesis_versions",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("extraction_schema_id", False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("scope", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("statements", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("meta_analysis", pg.JSONB, nullable=False, server_default=_obj()),
        _uuid("created_by"),
        _uuid("accepted_by"),
        _ts("accepted_at", nullable=True),
        sa.Column("accept_note", sa.Text),
        _uuid("supersedes_id"),
        _uuid("evidence_output_id"),
        _uuid("model_run_id"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_synthesis_versions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_synthesis_versions_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_schema_id"],
            ["extraction_schemas.id"],
            name="fk_synthesis_versions_extraction_schema_id_extraction_schemas",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["synthesis_versions.id"],
            name="fk_synthesis_versions_supersedes_id_synthesis_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_output_id"],
            ["evidence_outputs.id"],
            name="fk_synthesis_versions_evidence_output_id_evidence_outputs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"], ["model_runs.id"], name="fk_synthesis_versions_model_run_id_model_runs", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_synthesis_versions_project_version"),
        sa.CheckConstraint("status in ('draft','accepted','superseded')", name="status"),
    )
    op.create_index("ix_synthesis_versions_project", "synthesis_versions", ["project_id"])


def downgrade() -> None:
    op.drop_table("synthesis_versions")
    op.execute("DROP TRIGGER IF EXISTS appraisal_revisions_append_only ON appraisal_revisions")
    op.execute("DROP FUNCTION IF EXISTS appraisal_revision_guard()")
    op.drop_table("appraisal_revisions")
    op.drop_table("appraisal_records")
    op.execute("DELETE FROM evidence_outputs WHERE kind = 'synthesis'")
    op.execute("ALTER TABLE evidence_outputs DROP CONSTRAINT ck_evidence_outputs_kind")
    op.execute("ALTER TABLE evidence_outputs ADD CONSTRAINT ck_evidence_outputs_kind CHECK (kind in ('csv_export'))")
