"""Add opt-in per-team response caching."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_team_response_cache"
down_revision: str | None = "0003_usage_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a disabled-by-default cache opt-in to teams."""
    op.add_column(
        "teams", sa.Column("cache_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    """Remove the team cache opt-in."""
    op.drop_column("teams", "cache_enabled")
