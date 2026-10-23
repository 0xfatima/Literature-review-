"""Phase 4: scholarly search. search_runs (one user-started search, bound to an approved protocol version),
search_queries (the exact query sent to each provider, with cursor and counts), paper_records (deduplicated papers,
identifiers unique per project) and paper_source_records (every provider record as returned: provenance).
Adds the 'search' kind to workflow_events.

Revision ID: 0004_scholarly_search
Revises: 0003_protocol_workflow
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004_scholarly_search"
down_revision = "0003_protocol_workflow"
branch_labels = None
depends_on = None

OLD_KINDS = "('started','question','answer','stage','error','needs_attention','recovered','revision')"
NEW_KINDS = "('started','question','answer','stage','error','needs_attention','recovered','revision','search')"


def _now() -> sa.TextClause:
    return sa.text("now()")


def _list() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _obj() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "search_runs",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("protocol_version_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", pg.UUID(as_uuid=True)),
        sa.Column("tier", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("expression", sa.Text, nullable=False),
        sa.Column("expression_source", sa.String(24), nullable=False),
        sa.Column("derived_expression", sa.Text),
        sa.Column("derivation_notes", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("filters", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("providers", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("protocol_sources", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("limitations", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("records_retrieved", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("papers_new", sa.Integer, nullable=False, server_default="0"),
        sa.Column("papers_matched_existing", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplicates_in_run", sa.Integer, nullable=False, server_default="0"),
        sa.Column("possible_duplicates", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stop_reason", sa.String(32)),
        sa.Column("error_summary", sa.Text),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("broaden_decision", sa.String(8)),
        sa.Column("broaden_decided_at", sa.DateTime(timezone=True)),
        sa.Column("broaden_decided_by", pg.UUID(as_uuid=True)),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_search_runs"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_search_runs_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"], ["protocol_versions.id"], name="fk_search_runs_protocol_version_id_protocol_versions"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name="fk_search_runs_job_id_jobs", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["broaden_decided_by"], ["users.id"], name="fk_search_runs_broaden_decided_by_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_search_runs_created_by_users", ondelete="SET NULL"),
        sa.CheckConstraint("tier in ('open_access','additional')", name=op.f("ck_search_runs_tier")),
        sa.CheckConstraint(
            "status in ('queued','running','completed','partial','failed','cancelled')", name=op.f("ck_search_runs_status")
        ),
        sa.CheckConstraint(
            "expression_source in ('search_strategy','framework_elements','objective','topic','user_edited')",
            name=op.f("ck_search_runs_expression_source"),
        ),
        sa.CheckConstraint(
            "broaden_decision IS NULL OR broaden_decision in ('yes','no','later')", name=op.f("ck_search_runs_broaden")
        ),
        sa.CheckConstraint(
            "records_retrieved >= 0 AND records_skipped >= 0 AND papers_new >= 0 AND papers_matched_existing >= 0 "
            "AND duplicates_in_run >= 0 AND possible_duplicates >= 0",
            name=op.f("ck_search_runs_counts"),
        ),
    )
    op.create_index("ix_search_runs_project_created", "search_runs", ["project_id", "created_at"])
    op.create_index(
        "uq_search_runs_one_active",
        "search_runs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )

    op.create_table(
        "search_queries",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("search_run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("endpoint", sa.String(300), nullable=False),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("params", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("filters_applied", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("limitations", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("cursor", sa.Text),
        sa.Column("pages_fetched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_reported", sa.Integer),
        sa.Column("records_retrieved", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("error_kind", sa.String(32)),
        sa.Column("error_summary", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_search_queries"),
        sa.ForeignKeyConstraint(
            ["search_run_id"], ["search_runs.id"], name="fk_search_queries_search_run_id_search_runs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_search_queries_project_id_projects", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("search_run_id", "provider", name="uq_search_queries_search_run_id"),
        sa.CheckConstraint(
            "status in ('pending','running','completed','capped','partial','failed','cancelled')",
            name=op.f("ck_search_queries_status"),
        ),
        sa.CheckConstraint(
            "pages_fetched >= 0 AND records_retrieved >= 0 AND records_skipped >= 0",
            name=op.f("ck_search_queries_counts"),
        ),
    )
    op.create_index("ix_search_queries_project", "search_queries", ["project_id"])

    op.create_table(
        "paper_records",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("title_key", sa.String(500), nullable=False),
        sa.Column("authors", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("first_author_key", sa.String(120)),
        sa.Column("abstract", sa.Text),
        sa.Column("year", sa.Integer),
        sa.Column("venue", sa.String(500)),
        sa.Column("publication_type", sa.String(64)),
        sa.Column("keywords", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("language", sa.String(8)),
        sa.Column("doi", sa.String(255)),
        sa.Column("pmid", sa.String(16)),
        sa.Column("pmcid", sa.String(16)),
        sa.Column("arxiv_id", sa.String(64)),
        sa.Column("openalex_id", sa.String(32)),
        sa.Column("other_ids", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("is_preprint", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("has_preprint_version", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_retracted", sa.Boolean),
        sa.Column("oa_status", sa.String(8), nullable=False, server_default="unknown"),
        sa.Column("oa_url", sa.Text),
        sa.Column("pdf_url", sa.Text),
        sa.Column("license", sa.String(200)),
        sa.Column("access_status", sa.String(24), nullable=False, server_default="metadata_only"),
        sa.Column("full_text_status", sa.String(16), nullable=False, server_default="not_obtained"),
        sa.Column("field_sources", pg.JSONB, nullable=False, server_default=_obj()),
        sa.Column("providers", pg.JSONB, nullable=False, server_default=_list()),
        sa.Column("source_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("possible_duplicate_of_id", pg.UUID(as_uuid=True)),
        sa.Column("possible_duplicate_reason", sa.Text),
        sa.Column("first_seen_run_id", pg.UUID(as_uuid=True)),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_paper_records"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_paper_records_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["possible_duplicate_of_id"],
            ["paper_records.id"],
            name="fk_paper_records_possible_duplicate_of_id_paper_records",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_run_id"],
            ["search_runs.id"],
            name="fk_paper_records_first_seen_run_id_search_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("oa_status in ('open','closed','unknown')", name=op.f("ck_paper_records_oa_status")),
        sa.CheckConstraint(
            "access_status in ('open_pdf_link','open_landing_page','metadata_only')",
            name=op.f("ck_paper_records_access_status"),
        ),
        sa.CheckConstraint(
            "full_text_status in ('not_obtained','uploaded','parsed','failed')",
            name=op.f("ck_paper_records_full_text_status"),
        ),
        sa.CheckConstraint("source_count >= 0", name=op.f("ck_paper_records_source_count")),
    )
    for name, col in (
        ("uq_paper_records_project_doi", "doi"),
        ("uq_paper_records_project_pmid", "pmid"),
        ("uq_paper_records_project_arxiv", "arxiv_id"),
        ("uq_paper_records_project_openalex", "openalex_id"),
    ):
        op.create_index(
            name, "paper_records", ["project_id", col], unique=True, postgresql_where=sa.text(f"{col} IS NOT NULL")
        )
    op.create_index("ix_paper_records_project_title_key", "paper_records", ["project_id", "title_key"])
    op.create_index("ix_paper_records_project_created", "paper_records", ["project_id", "created_at"])

    op.create_table(
        "paper_source_records",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("search_run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("search_query_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_record_id", sa.String(255), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("raw", pg.JSONB, nullable=False),
        sa.Column("normalized", pg.JSONB, nullable=False),
        sa.Column("match_basis", sa.String(24), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_paper_source_records"),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["paper_records.id"], name="fk_paper_source_records_paper_id_paper_records", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_paper_source_records_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["search_run_id"],
            ["search_runs.id"],
            name="fk_paper_source_records_search_run_id_search_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["search_query_id"],
            ["search_queries.id"],
            name="fk_paper_source_records_search_query_id_search_queries",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "search_query_id", "provider_record_id", name="uq_paper_source_records_search_query_id"
        ),
        sa.CheckConstraint(
            "match_basis in ('doi','pmid','arxiv_id','openalex_id','title_author_year','new')",
            name=op.f("ck_paper_source_records_match_basis"),
        ),
        sa.CheckConstraint("rank >= 1", name=op.f("ck_paper_source_records_rank")),
    )
    op.create_index("ix_paper_source_records_paper", "paper_source_records", ["paper_id"])
    op.create_index("ix_paper_source_records_run", "paper_source_records", ["search_run_id"])
    op.create_index("ix_paper_source_records_project", "paper_source_records", ["project_id"])

    op.drop_constraint(op.f("ck_workflow_events_kind"), "workflow_events", type_="check")
    op.create_check_constraint(op.f("ck_workflow_events_kind"), "workflow_events", f"kind in {NEW_KINDS}")


def downgrade() -> None:
    op.execute("DELETE FROM workflow_events WHERE kind = 'search'")
    op.drop_constraint(op.f("ck_workflow_events_kind"), "workflow_events", type_="check")
    op.create_check_constraint(op.f("ck_workflow_events_kind"), "workflow_events", f"kind in {OLD_KINDS}")
    op.drop_table("paper_source_records")
    op.drop_table("paper_records")
    op.drop_table("search_queries")
    op.drop_table("search_runs")
