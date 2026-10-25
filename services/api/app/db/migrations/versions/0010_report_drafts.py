"""Phase 11: review report drafts (draft_versions), citations (claim_evidence_links) and recorded exports
(report_exports).

Revision ID: 0010_report_drafts
Revises: 0009_appraisal_synthesis
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010_report_drafts"
down_revision = "0009_appraisal_synthesis"
branch_labels = None
depends_on = None


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def _now_col(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def _json(name: str, empty: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.JSONB, nullable=False, server_default=sa.text(f"'{empty}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "draft_versions",
        _uuid("id", False),
        _uuid("project_id", False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="current"),
        sa.Column("title", sa.Text, nullable=False),
        _json("sections", "[]"),
        _json("references", "[]"),
        _json("review_state", "{}"),
        _json("validation", "{}"),
        _uuid("synthesis_version_id"),
        _uuid("protocol_version_id"),
        _uuid("extraction_schema_id"),
        _uuid("supersedes_id"),
        sa.Column("change_summary", sa.Text),
        _uuid("created_by"),
        _now_col("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_draft_versions"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_draft_versions_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["synthesis_version_id"],
            ["synthesis_versions.id"],
            name="fk_draft_versions_synthesis_version_id_synthesis_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            name="fk_draft_versions_protocol_version_id_protocol_versions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_schema_id"],
            ["extraction_schemas.id"],
            name="fk_draft_versions_extraction_schema_id_extraction_schemas",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["draft_versions.id"],
            name="fk_draft_versions_supersedes_id_draft_versions",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_draft_versions_project_version"),
        sa.CheckConstraint("status in ('current','superseded')", name="status"),
    )
    op.create_index("ix_draft_versions_project", "draft_versions", ["project_id"])

    op.create_table(
        "claim_evidence_links",
        _uuid("id", False),
        _uuid("draft_version_id", False),
        sa.Column("claim_id", sa.String(64), nullable=False),
        _uuid("paper_id", False),
        _uuid("evidence_item_id"),
        sa.Column("evidence_revision", sa.Integer),
        sa.Column("citation_key", sa.String(64), nullable=False),
        sa.Column("citation_locator", sa.String(64)),
        sa.PrimaryKeyConstraint("id", name="pk_claim_evidence_links"),
        sa.ForeignKeyConstraint(
            ["draft_version_id"],
            ["draft_versions.id"],
            name="fk_claim_evidence_links_draft_version_id_draft_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_claim_evidence_links_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id"],
            ["evidence_items.id"],
            name="fk_claim_evidence_links_evidence_item_id_evidence_items",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_claim_evidence_links_draft", "claim_evidence_links", ["draft_version_id"])
    op.create_index("ix_claim_evidence_links_paper", "claim_evidence_links", ["paper_id"])

    op.create_table(
        "report_exports",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("draft_version_id", False),
        sa.Column("format", sa.String(24), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer, nullable=False),
        _json("review_state", "{}"),
        _json("validation_summary", "{}"),
        sa.Column("data_changed_since_draft", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _uuid("created_by"),
        _now_col("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_report_exports"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_report_exports_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["draft_version_id"],
            ["draft_versions.id"],
            name="fk_report_exports_draft_version_id_draft_versions",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "format in ('docx','markdown','supplementary_zip','prisma_json')", name="format"
        ),
    )
    op.create_index("ix_report_exports_project_created", "report_exports", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("report_exports")
    op.drop_table("claim_evidence_links")
    op.drop_table("draft_versions")
