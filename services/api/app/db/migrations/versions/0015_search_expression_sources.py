"""Allow AI-built and topic+objective search expressions to be recorded on a search run.

search_runs.expression_source was limited to the Phase 4 sources, so a search run whose query had been designed by
the AI model ("ai", added in 0014) or built from the topic and the objective together ("objective_topic") failed
the check constraint when it was saved.

Revision ID: 0015_search_expression_sources
Revises: 0014_accounts_ai_keys_search
"""

from alembic import op

revision = "0015_search_expression_sources"
down_revision = "0014_accounts_ai_keys_search"
branch_labels = None
depends_on = None

OLD = "('search_strategy','framework_elements','objective','topic','user_edited')"
NEW = "('search_strategy','framework_elements','ai','objective','objective_topic','topic','user_edited')"
NAME = "ck_search_runs_expression_source"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), "search_runs", type_="check")
    op.create_check_constraint(op.f(NAME), "search_runs", f"expression_source in {NEW}")


def downgrade() -> None:
    # Runs recorded with a newer source keep their query; only the label falls back to "user_edited".
    op.execute(
        "UPDATE search_runs SET expression_source = 'user_edited' "
        "WHERE expression_source IN ('ai', 'objective_topic')"
    )
    op.drop_constraint(op.f(NAME), "search_runs", type_="check")
    op.create_check_constraint(op.f(NAME), "search_runs", f"expression_source in {OLD}")
