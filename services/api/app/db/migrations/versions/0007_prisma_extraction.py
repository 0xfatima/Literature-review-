"""Phase 7+8: studies, record_status_events (PRISMA), full_text screening stage,
study_id on paper_records, extraction_schemas, evidence_items.

Revision ID: 0007_prisma_extraction
Revises: 0006_documents
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007_prisma_extraction"
down_revision = "0006_documents"
branch_labels = None
depends_on = None

STATUS_EVENT_GUARD = """
CREATE FUNCTION record_status_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM projects WHERE id = OLD.project_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'record_status_events are append-only: correct by adding a new event'
    USING ERRCODE = 'restrict_violation';
END $$;
"""


def _now() -> sa.TextClause:
    return sa.text("now()")


def _obj() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _list() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    # 1. studies (must come before paper_records FK)
    op.create_table(
        "studies",
        _uuid("id", False),
        _uuid("project_id", False),
        sa.Column("short_name", sa.String(200)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_studies"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_studies_project_id_projects", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_studies_project", "studies", ["project_id"])

    # 2. study_id on paper_records
    op.add_column(
        "paper_records",
        sa.Column("study_id", pg.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_paper_records_study_id_studies",
        "paper_records",
        "studies",
        ["study_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_records_study "
        "ON paper_records (study_id) WHERE study_id IS NOT NULL"
    )

    # 3. record_status_events
    op.create_table(
        "record_status_events",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id", False),
        _uuid("protocol_version_id", False),
        sa.Column("pathway", sa.String(1), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(120)),
        sa.Column("reason_label", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column("source_tag", sa.String(32)),
        _uuid("actor_id"),
        _uuid("supersedes_id"),
        _uuid("screening_decision_id"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_record_status_events"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_record_status_events_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"],
            name="fk_record_status_events_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"], ["protocol_versions.id"],
            name="fk_record_status_events_protocol_version_id_protocol_versions"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["record_status_events.id"],
            name="fk_record_status_events_supersedes_id_record_status_events"
        ),
        sa.ForeignKeyConstraint(
            ["screening_decision_id"], ["screening_decisions.id"],
            name="fk_rse_screening_decision_id",
            ondelete="SET NULL"
        ),
    )
    op.create_index("ix_record_status_events_paper", "record_status_events", ["paper_id"])
    op.create_index(
        "ix_record_status_events_project_stage",
        "record_status_events", ["project_id", "stage"]
    )
    op.create_index(
        "ix_record_status_events_project_pv",
        "record_status_events", ["project_id", "protocol_version_id"]
    )
    op.execute("ALTER TABLE record_status_events ADD CONSTRAINT ck_record_status_events_pathway CHECK (pathway in ('A','B'))")
    op.execute(
        "ALTER TABLE record_status_events ADD CONSTRAINT ck_record_status_events_stage "
        "CHECK (stage in ('identified','removed_before_screen','ta_excluded','ta_included',"
        "'not_retrieved','ft_excluded','included'))"
    )

    # append-only trigger
    op.execute(STATUS_EVENT_GUARD)
    op.execute(
        "CREATE TRIGGER record_status_events_append_only "
        "BEFORE UPDATE OR DELETE ON record_status_events "
        "FOR EACH ROW EXECUTE FUNCTION record_status_event_guard()"
    )

    # 4. extend SCREENING_STAGES check on screening_decisions and screening_states to allow full_text
    op.execute("ALTER TABLE screening_decisions DROP CONSTRAINT ck_screening_decisions_stage")
    op.execute("ALTER TABLE screening_decisions ADD CONSTRAINT ck_screening_decisions_stage CHECK (stage in ('title_abstract','full_text'))")
    op.execute("ALTER TABLE screening_states DROP CONSTRAINT ck_screening_states_stage")
    op.execute("ALTER TABLE screening_states ADD CONSTRAINT ck_screening_states_stage CHECK (stage in ('title_abstract','full_text'))")
    # full_text exclusion must always have a reason_code
    op.execute("ALTER TABLE screening_decisions ADD CONSTRAINT ck_screening_decisions_ft_reason CHECK (stage != 'full_text' OR decision != 'exclude' OR reason_code IS NOT NULL)")

    # 5. extraction_schemas
    op.create_table(
        "extraction_schemas",
        _uuid("id", False),
        _uuid("project_id", False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("field_definitions", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("question_map", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("notes", sa.Text),
        _uuid("approved_by"),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("change_reason", sa.Text),
        _uuid("supersedes_id"),
        _uuid("created_by"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_schemas"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_extraction_schemas_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["extraction_schemas.id"],
            name="fk_extraction_schemas_supersedes_id_extraction_schemas", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_extraction_schemas_project_version"),
    )
    op.create_index("ix_extraction_schemas_project", "extraction_schemas", ["project_id"])
    op.execute("ALTER TABLE extraction_schemas ADD CONSTRAINT ck_extraction_schemas_status CHECK (status in ('draft','approved'))")
    op.execute("ALTER TABLE extraction_schemas ADD CONSTRAINT ck_extraction_schemas_version_positive CHECK (version >= 1)")

    # 6. evidence_items
    op.create_table(
        "evidence_items",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id", False),
        _uuid("extraction_schema_id", False),
        sa.Column("field_key", sa.String(80), nullable=False),
        sa.Column("value", pg.JSONB),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        _uuid("document_page_id"),
        sa.Column("excerpt", sa.Text),
        _uuid("reviewer_id"),
        _uuid("model_run_id"),
        sa.Column("note", sa.Text),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_items"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_evidence_items_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"],
            name="fk_evidence_items_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_schema_id"], ["extraction_schemas.id"],
            name="fk_evidence_items_extraction_schema_id_extraction_schemas", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_page_id"], ["document_pages.id"],
            name="fk_evidence_items_document_page_id_document_pages", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"],
            name="fk_evidence_items_reviewer_id_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"], ["model_runs.id"],
            name="fk_evidence_items_model_run_id_model_runs", ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "paper_id", "extraction_schema_id", "field_key",
            name="uq_evidence_items_paper_schema_field"
        ),
    )
    op.create_index("ix_evidence_items_project_schema", "evidence_items", ["project_id", "extraction_schema_id"])
    op.create_index("ix_evidence_items_paper", "evidence_items", ["paper_id"])
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_status "
        "CHECK (status in ('pending','found','not_located','unavailable','not_applicable'))"
    )
    op.execute(
        "ALTER TABLE evidence_items ADD CONSTRAINT ck_evidence_items_found_has_value "
        "CHECK (status != 'found' OR value IS NOT NULL)"
    )


def downgrade() -> None:
    op.drop_table("evidence_items")
    op.drop_table("extraction_schemas")
    op.execute("DROP TRIGGER IF EXISTS record_status_events_append_only ON record_status_events")
    op.execute("DROP FUNCTION IF EXISTS record_status_event_guard()")
    op.drop_table("record_status_events")
    op.drop_constraint("fk_paper_records_study_id_studies", "paper_records", type_="foreignkey")
    op.drop_index("ix_paper_records_study", "paper_records")
    op.drop_column("paper_records", "study_id")
    op.drop_table("studies")
    op.execute("ALTER TABLE screening_decisions DROP CONSTRAINT ck_screening_decisions_ft_reason")
    # Full-text screening did not exist before this revision, so its rows cannot be represented below it; without
    # this, re-adding the 'title_abstract'-only checks fails on any database that has a full-text decision.
    # DESTRUCTIVE (as is dropping this revision's tables above): back up before downgrading past 0007.
    # screening_decisions is append-only by trigger (0005), so the trigger is disabled for this one statement.
    op.execute("DELETE FROM screening_states WHERE stage = 'full_text'")
    op.execute("ALTER TABLE screening_decisions DISABLE TRIGGER screening_decisions_append_only")
    op.execute("DELETE FROM screening_decisions WHERE stage = 'full_text'")
    op.execute("ALTER TABLE screening_decisions ENABLE TRIGGER screening_decisions_append_only")
    op.execute("ALTER TABLE screening_decisions DROP CONSTRAINT ck_screening_decisions_stage")
    op.create_check_constraint("ck_screening_decisions_stage", "screening_decisions", "stage in ('title_abstract')")
    op.execute("ALTER TABLE screening_states DROP CONSTRAINT ck_screening_states_stage")
    op.create_check_constraint("ck_screening_states_stage", "screening_states", "stage in ('title_abstract')")
