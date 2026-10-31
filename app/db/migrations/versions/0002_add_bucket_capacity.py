"""Add optional explicit request and token burst capacities."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_bucket_capacity"
down_revision: str | None = "0001_teams_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable per-team burst overrides."""
    op.add_column("teams", sa.Column("rpm_burst_capacity", sa.Integer(), nullable=True))
    op.add_column("teams", sa.Column("tpm_burst_capacity", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove explicit per-team burst overrides."""
    op.drop_column("teams", "tpm_burst_capacity")
    op.drop_column("teams", "rpm_burst_capacity")
