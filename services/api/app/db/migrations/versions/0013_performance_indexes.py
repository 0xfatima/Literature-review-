"""Phase 15: indexes for the new hot paths, chosen by EXPLAIN ANALYZE on 300k jobs / 200k model runs
(docs/performance.md#indexes):

- jobs(finished_at) WHERE finished_at IS NOT NULL: metrics window scan 72 ms (seq scan) -> 0.5 ms.
- model_runs(created_at) INCLUDE (input_tokens, output_tokens): the deployment-wide token budget check that runs
  before every AI call, 46 ms (seq scan) -> 12.5 ms (index-only scan over the last 24 h only).

Built CONCURRENTLY (outside a transaction) so upgrading a large production database never blocks writes.

Revision ID: 0013_performance_indexes
Revises: 0012_audit_hardening
"""

from alembic import op

revision = "0013_performance_indexes"
down_revision = "0012_audit_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_finished_at ON jobs (finished_at) "
            "WHERE finished_at IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_model_runs_created_tokens ON model_runs (created_at) "
            "INCLUDE (input_tokens, output_tokens)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_model_runs_created_tokens")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_finished_at")
