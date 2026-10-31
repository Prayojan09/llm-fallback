"""Validated retry, timeout, and circuit-breaker configuration."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class FallbackPolicy(BaseModel):
    """Runtime tunables for retries, deadlines, and shared breaker state."""

    retries_per_provider: int = Field(ge=0)
    retry_base_seconds: float = Field(ge=0)
    retry_max_seconds: float = Field(gt=0)
    retry_jitter_seconds: float = Field(ge=0)
    attempt_timeout_seconds: float = Field(gt=0)
    overall_timeout_seconds: float = Field(gt=0)
    breaker_failure_threshold: int = Field(ge=1)
    breaker_failure_window_seconds: int = Field(gt=0)
    breaker_cooldown_seconds: int = Field(gt=0)
    breaker_half_open_probes: int = Field(ge=1)
    breaker_redis_failure_policy: Literal["open", "closed"]

    @classmethod
    def load(cls, config_path: str) -> "FallbackPolicy":
        """Load and validate fallback policy from YAML."""
        values = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(values)
