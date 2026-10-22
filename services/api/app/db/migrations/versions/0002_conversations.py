"""Phase 2: project-scoped chat threads (conversations) and their messages.

Revision ID: 0002_conversations
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002_conversations"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("title_source", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_conversations_project_id_projects", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_conversations_created_by_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint("title_source in ('auto','user')", name=op.f("ck_conversations_title_source")),
        sa.CheckConstraint("message_count >= 0", name=op.f("ck_conversations_message_count")),
    )
    op.create_index("ix_conversations_project_activity", "conversations", ["project_id", "updated_at"])

    op.create_table(
        "messages",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("author_id", pg.UUID(as_uuid=True)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB),
        sa.Column("client_message_id", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name="fk_messages_author_id_users", ondelete="SET NULL"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_messages_conversation_id"),
        sa.CheckConstraint("role in ('user','assistant','system')", name=op.f("ck_messages_role")),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_messages_seq")),
    )
    # A retried send (same client-generated id) must not create a second message.
    op.execute(
        "CREATE UNIQUE INDEX uq_messages_conversation_client_id ON messages (conversation_id, client_message_id) "
        "WHERE client_message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
