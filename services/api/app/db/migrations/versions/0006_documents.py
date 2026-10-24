"""Phase 6: paper_documents (uploaded PDFs) and document_pages (page-level extracted text).

Revision ID: 0006_documents
Revises: 0005_relevance_screening
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006_documents"
down_revision = "0005_relevance_screening"
branch_labels = None
depends_on = None

LINKED = "('auto_matched','user_linked','confirmed')"


def _now() -> sa.TextClause:
    return sa.text("now()")


def _uuid(name: str, nullable: bool = True) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, pg.UUID(as_uuid=True), nullable=nullable)


def _int0(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.Integer, nullable=False, server_default="0")


def upgrade() -> None:
    op.create_table(
        "paper_documents",
        _uuid("id", False),
        _uuid("project_id", False),
        _uuid("paper_id"),
        _uuid("uploader_id", False),
        sa.Column("storage_key", sa.String(300)),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("parse_status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("error_code", sa.String(48)),
        sa.Column("error_message", sa.Text),
        sa.Column("error_retryable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("page_count", sa.Integer),
        _int0("text_version"),
        _int0("pages_text"),
        _int0("pages_ocr"),
        _int0("pages_blank"),
        _int0("pages_unreadable"),
        sa.Column("pdf_metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("scan_status", sa.String(12), nullable=False, server_default="not_scanned"),
        sa.Column("scan_detail", sa.String(200)),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="unmatched"),
        sa.Column("match_method", sa.String(24)),
        sa.Column("match_score", sa.Float),
        sa.Column("match_candidates", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        _uuid("matched_by"),
        sa.Column("matched_at", sa.DateTime(timezone=True)),
        _uuid("processing_job_id"),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        _uuid("removed_by"),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_paper_documents"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_paper_documents_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_paper_documents_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["uploader_id"], ["users.id"], name="fk_paper_documents_uploader_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id"], ["jobs.id"], name="fk_paper_documents_processing_job_id_jobs", ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "parse_status in ('queued','processing','parsed','failed')", name=op.f("ck_paper_documents_parse_status")
        ),
        sa.CheckConstraint(
            "match_status in ('unmatched','needs_confirmation','auto_matched','user_linked','confirmed')",
            name=op.f("ck_paper_documents_match_status"),
        ),
        sa.CheckConstraint(
            "scan_status in ('not_scanned','clean','infected','error')", name=op.f("ck_paper_documents_scan_status")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_paper_documents_size")),
        sa.CheckConstraint("text_version >= 0", name=op.f("ck_paper_documents_text_version")),
        sa.CheckConstraint(
            f"match_status NOT IN {LINKED} OR paper_id IS NOT NULL", name=op.f("ck_paper_documents_linked_has_paper")
        ),
        sa.CheckConstraint(
            "parse_status <> 'failed' OR error_code IS NOT NULL", name=op.f("ck_paper_documents_failed_has_code")
        ),
    )
    op.create_index(
        "uq_paper_documents_project_sha256_active",
        "paper_documents",
        ["project_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index("ix_paper_documents_project_created", "paper_documents", ["project_id", "created_at"])
    op.create_index("ix_paper_documents_paper", "paper_documents", ["paper_id"])
    op.create_index(
        "ix_paper_documents_removed",
        "paper_documents",
        ["removed_at"],
        postgresql_where=sa.text("removed_at IS NOT NULL AND purged_at IS NULL"),
    )
    op.create_index("ix_paper_documents_job", "paper_documents", ["processing_job_id"])

    op.create_table(
        "document_pages",
        _uuid("id", False),
        _uuid("document_id", False),
        _uuid("project_id", False),
        sa.Column("page_no", sa.Integer, nullable=False),
        sa.Column("text_version", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("extraction", sa.String(12), nullable=False),
        sa.Column("char_count", sa.Integer, nullable=False),
        sa.Column("ocr_confidence", sa.Float),
        sa.Column("warnings", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("id", name="pk_document_pages"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["paper_documents.id"],
            name="fk_document_pages_document_id_paper_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_document_pages_project_id_projects", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("document_id", "page_no", "text_version", name="uq_document_pages_document_id"),
        sa.CheckConstraint(
            "extraction in ('text','ocr','blank','unreadable')", name=op.f("ck_document_pages_extraction")
        ),
        sa.CheckConstraint("page_no >= 1 AND text_version >= 1", name=op.f("ck_document_pages_numbers")),
    )
    op.create_index("ix_document_pages_project", "document_pages", ["project_id"])


def downgrade() -> None:
    op.drop_table("document_pages")
    op.drop_table("paper_documents")
