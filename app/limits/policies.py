"""Configuration-file policy for rate-limit behavior and estimates."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class RateLimitPolicy(BaseModel):
    """Tunable parameters for Redis buckets and pre-call token estimates."""

    request_burst_multiplier: float = Field(gt=0)
    token_burst_multiplier: float = Field(gt=0)
    default_completion_tokens: int = Field(gt=0)
    characters_per_token_estimate: int = Field(gt=0)
    redis_failure_policy: Literal["open", "closed"]
    minimum_key_ttl_seconds: int = Field(gt=0)
    budget_warn_ratio: float = Field(gt=0, le=1)
    usage_queue_size: int = Field(gt=0)

    @classmethod
    def load(cls, config_path: str) -> "RateLimitPolicy":
        """Load and validate the rate-limit policy YAML file."""
        values = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(values)
