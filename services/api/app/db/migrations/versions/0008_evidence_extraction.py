"""Phase 9: evidence extraction with provenance.

evidence_items gains origin / review_status / extraction_version / pages_inspected / validation and the
`ambiguous` status; new append-only evidence_item_revisions; evidence_outputs + evidence_output_items record
which revision of each claim an output (CSV export) was generated from; llm_consents (gate G3) records the project
owner's consent to sending private document text to one provider/model.

Revision ID: 0008_evidence_extraction
Revises: 0007_prisma_extraction
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008_evidence_extraction"
down_revision = "0007_prisma_extraction"
branch_labels = None
depends_on = None

OLD_STATUSES = "('pending','found','not_located','unavailable','not_applicable')"
NEW_STATUSES = "('pending','found','not_located','ambiguous','unavailable','not_applicable')"

REVISION_GUARD = """
CREATE FUNCTION evidence_item_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM evidence_items WHERE id = OLD.evidence_item_id) THEN
    RETURN OLD;  -- cascaded removal of the item (project, paper or schema deleted)
  END IF;
  RAISE EXCEPTION 'evidence_item_revisions are append-only'
    USING ERRCODE = 'restrict_violation';
END $$;
"""


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.add_column("evidence_items", sa.Column("origin", sa.String(8), nullable=False, server_default="manual"))
    op.add_column(
        "evidence_items", sa.Column("review_status", sa.String(12), nullable=False, server_default="unreviewed")
    )
    op.add_column("evidence_items", sa.Column("extraction_version", sa.String(48)))
    op.add_column(
        "evidence_items",
        sa.Column("pages_inspected", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "evidence_items", sa.Column("validation", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    )
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_status")
    op.execute(f"ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_status CHECK (status in {NEW_STATUSES})")
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_origin CHECK (origin in ('manual','ai','system'))"
    )
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_review_status "
        "CHECK (review_status in ('unreviewed','approved','edited'))"
    )

    op.create_table(
        "evidence_item_revisions",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("evidence_item_id", False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("before", pg.JSONB),
        sa.Column("after", pg.JSONB, nullable=False),
        _uuid("actor_id"),
        _uuid("model_run_id"),
        sa.Column("reason", sa.Text),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_item_revisions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_evidence_item_revisions_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id"],
            ["evidence_items.id"],
            name="fk_evidence_item_revisions_evidence_item_id_evidence_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["model_runs.id"],
            name="fk_evidence_item_revisions_model_run_id_model_runs",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("evidence_item_id", "revision", name="uq_evidence_item_revisions_item_revision"),
    )
    op.create_index("ix_evidence_item_revisions_project", "evidence_item_revisions", ["project_id"])
    op.execute(REVISION_GUARD)
    op.execute(
        "CREATE TRIGGER evidence_item_revisions_append_only BEFORE UPDATE OR DELETE ON evidence_item_revisions "
        "FOR EACH ROW EXECUTE FUNCTION evidence_item_revision_guard()"
    )

    op.create_table(
        "evidence_outputs",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("extraction_schema_id", False),
        sa.Column("kind", sa.String(24), nullable=False),
        _uuid("created_by"),
        sa.Column("item_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_outputs"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_evidence_outputs_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_schema_id"],
            ["extraction_schemas.id"],
            name="fk_evidence_outputs_extraction_schema_id_extraction_schemas",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("kind in ('csv_export')", name="kind"),
    )
    op.create_index("ix_evidence_outputs_project_created", "evidence_outputs", ["project_id", "created_at"])

    op.create_table(
        "evidence_output_items",
        _uuid("id", False),
        _uuid("evidence_output_id", False),
        _uuid("evidence_item_id", False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_output_items"),
        sa.ForeignKeyConstraint(
            ["evidence_output_id"],
            ["evidence_outputs.id"],
            name="fk_evidence_output_items_evidence_output_id_evidence_outputs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id"],
            ["evidence_items.id"],
            name="fk_evidence_output_items_evidence_item_id_evidence_items",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("evidence_output_id", "evidence_item_id", name="uq_evidence_output_items_output_item"),
    )
    op.create_index("ix_evidence_output_items_item", "evidence_output_items", ["evidence_item_id"])

    op.create_table(
        "llm_consents",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("user_id", False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("data_class", sa.String(24), nullable=False),
        sa.Column("terms_record_version", sa.String(64), nullable=False),
        sa.Column("notice_version", sa.String(32), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_llm_consents"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_llm_consents_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_llm_consents_user_id_users", ondelete="CASCADE"),
        sa.CheckConstraint(
            "data_class in ('public_metadata','private_document')", name="data_class"
        ),
    )
    op.create_index(
        "uq_llm_consents_active",
        "llm_consents",
        ["project_id", "provider", "model", "data_class"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("llm_consents")
    op.drop_table("evidence_output_items")
    op.drop_table("evidence_outputs")
    op.execute("DROP TRIGGER IF EXISTS evidence_item_revisions_append_only ON evidence_item_revisions")
    op.execute("DROP FUNCTION IF EXISTS evidence_item_revision_guard()")
    op.drop_table("evidence_item_revisions")
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_review_status")
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_origin")
    op.execute("ALTER TABLE evidence_items DROP CONSTRAINT ck_evidence_items_status")
    op.execute("UPDATE evidence_items SET status = 'pending' WHERE status = 'ambiguous'")
    op.execute(f"ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_status CHECK (status in {OLD_STATUSES})")
    for col in ("validation", "pages_inspected", "extraction_version", "review_status", "origin"):
        op.drop_column("evidence_items", col)
