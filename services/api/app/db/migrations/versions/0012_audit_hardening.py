"""Phase 14: audit attribution for system processes and an index for account-level audit views.

Adding a nullable column and an index does not touch existing rows, so the append-only trigger is not involved.

Revision ID: 0012_audit_hardening
Revises: 0011_pipeline
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_audit_hardening"
down_revision = "0011_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("actor_process", sa.String(64), nullable=True))
    op.create_index("ix_audit_events_entity_at", "audit_events", ["entity_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_entity_at", table_name="audit_events")
    op.drop_column("audit_events", "actor_process")
