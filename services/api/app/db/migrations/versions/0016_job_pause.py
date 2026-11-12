"""jobs.paused_at: a step the person paused (a remembered cancel) so it can be resumed from where it stopped.

Revision ID: 0016_job_pause
Revises: 0015_search_expression_sources
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_job_pause"
down_revision = "0015_search_expression_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "paused_at")
