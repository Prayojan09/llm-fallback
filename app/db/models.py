"""SQLAlchemy models for teams and hashed gateway API keys."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative metadata base."""


class Team(Base):
    """A customer team with model and usage limits."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rpm_limit: Mapped[int] = mapped_column(default=60, nullable=False)
    tpm_limit: Mapped[int] = mapped_column(default=100_000, nullable=False)
    rpm_burst_capacity: Mapped[int | None] = mapped_column(nullable=True)
    tpm_burst_capacity: Mapped[int | None] = mapped_column(nullable=True)
    allowed_models: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["*"], nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cache_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class ApiKey(Base):
    """A team API key stored as a non-reversible hash and display prefix."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team: Mapped[Team] = relationship(back_populates="api_keys")


class UsageLedger(Base):
    """Usage, cost, provider, and outcome record for one gateway request."""

    __tablename__ = "usage_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    api_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    requested_model: Mapped[str] = mapped_column(String(200), nullable=False)
    served_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    served_model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
