"""Accounts, per-user AI settings and AI-built search expressions.

- users.email_verified_at (existing accounts are back-filled with created_at, so nobody is locked out);
- auth_tokens: one-time e-mail tokens (verification, password reset), stored as SHA-256 only;
- user_llm_settings: the provider/model a user chose and their encrypted API key;
- search_expression_suggestions: cached AI-built Boolean expression per approved protocol version.

Revision ID: 0014_accounts_ai_keys_search
Revises: 0013_performance_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014_accounts_ai_keys_search"
down_revision = "0013_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")

    op.create_table(
        "auth_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("purpose in ('verify_email','reset_password')", name="purpose"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_tokens"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_tokens_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    op.create_table(
        "user_llm_settings",
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text()),
        sa.Column("api_key_hint", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_llm_settings"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_llm_settings_user_id_users", ondelete="CASCADE"
        ),
    )

    op.create_table(
        "search_expression_suggestions",
        sa.Column("protocol_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("concepts", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("notes", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("protocol_version_id", name="pk_search_expression_suggestions"),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            name="fk_search_expression_suggestions_protocol_version",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("search_expression_suggestions")
    op.drop_table("user_llm_settings")
    op.drop_index("ix_auth_tokens_user_id", table_name="auth_tokens")
    op.drop_table("auth_tokens")
    op.drop_column("users", "email_verified_at")
