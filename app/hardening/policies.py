"""Validated hardening tunables loaded from configuration."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class HardeningPolicy(BaseModel):
    """Request size, cache, concurrency, and shutdown settings."""

    max_request_body_bytes: int = Field(gt=0)
    response_cache_ttl_seconds: int = Field(gt=0)
    provider_concurrency_default: int = Field(gt=0)
    provider_concurrency_limits: dict[str, int]
    provider_concurrency_wait_seconds: float = Field(ge=0)
    provider_concurrency_poll_seconds: float = Field(gt=0)
    provider_concurrency_lease_seconds: int = Field(gt=0)
    provider_concurrency_redis_failure_policy: Literal["open", "closed"]
    graceful_shutdown_timeout_seconds: float = Field(gt=0)

    @field_validator("provider_concurrency_limits")
    @classmethod
    def validate_concurrency_limits(cls, values: dict[str, int]) -> dict[str, int]:
        """Require positive explicit caps for configured provider names."""
        if any(value <= 0 for value in values.values()):
            raise ValueError("Provider concurrency limits must be positive")
        return values

    @classmethod
    def load(cls, config_path: str) -> "HardeningPolicy":
        """Read and validate hardening settings from YAML."""
        values = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(values)
