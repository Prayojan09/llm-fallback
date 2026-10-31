"""Pydantic request and response models for team administration and auth."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TeamCreate(BaseModel):
    """Fields needed to create a team."""

    name: str = Field(min_length=1, max_length=200)
    monthly_budget_usd: float = Field(default=0.0, ge=0)
    rpm_limit: int = Field(default=60, ge=1)
    tpm_limit: int = Field(default=100_000, ge=1)
    rpm_burst_capacity: int | None = Field(default=None, ge=1)
    tpm_burst_capacity: int | None = Field(default=None, ge=1)
    allowed_models: list[str] = Field(default_factory=lambda: ["*"])
    active: bool = True
    cache_enabled: bool = False


class TeamUpdate(BaseModel):
    """Optional fields for a partial team update."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    rpm_burst_capacity: int | None = Field(default=None, ge=1)
    tpm_burst_capacity: int | None = Field(default=None, ge=1)
    allowed_models: list[str] | None = None
    active: bool | None = None
    cache_enabled: bool | None = None


class TeamResponse(BaseModel):
    """Public team configuration without key material."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    monthly_budget_usd: float
    rpm_limit: int
    tpm_limit: int
    rpm_burst_capacity: int | None = None
    tpm_burst_capacity: int | None = None
    allowed_models: list[str]
    active: bool
    cache_enabled: bool = False
    created_at: datetime


class ApiKeyMetadata(BaseModel):
    """Safe-to-display API key metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    team_id: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyMetadata):
    """API key creation response containing the secret exactly once."""

    api_key: str


class ApiKeyRotationRequest(BaseModel):
    """Optional existing key to revoke in the same transaction as rotation."""

    revoke_key_id: str | None = None


class TeamAuth(BaseModel):
    """Small authenticated team snapshot suitable for Redis caching."""

    id: str
    api_key_id: str | None = None
    name: str
    monthly_budget_usd: float
    rpm_limit: int
    tpm_limit: int
    rpm_burst_capacity: int | None = None
    tpm_burst_capacity: int | None = None
    allowed_models: list[str]
    active: bool
    cache_enabled: bool = False
