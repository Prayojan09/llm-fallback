"""Create usage ledger for cost and request summaries."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_usage_ledger"
down_revision: str | None = "0002_bucket_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the append-oriented per-request usage ledger."""
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("team_id", sa.String(length=36), nullable=False),
        sa.Column("api_key_id", sa.String(length=36), nullable=True),
        sa.Column("requested_model", sa.String(length=200), nullable=False),
        sa.Column("served_provider", sa.String(length=100), nullable=False),
        sa.Column("served_model", sa.String(length=200), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_ledger_team_id", "usage_ledger", ["team_id"])
    op.create_index("ix_usage_ledger_created_at", "usage_ledger", ["created_at"])


def downgrade() -> None:
    """Drop the usage ledger."""
    op.drop_index("ix_usage_ledger_created_at", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_team_id", table_name="usage_ledger")
    op.drop_table("usage_ledger")
