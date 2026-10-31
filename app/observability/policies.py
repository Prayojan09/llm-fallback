"""Validated sampling and histogram settings for observability."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ObservabilityPolicy(BaseModel):
    """Configurable trace sampling and latency histogram bucket boundaries."""

    request_duration_buckets_seconds: list[float] = Field(min_length=1)
    trace_sample_ratio: float = Field(ge=0, le=1)

    @classmethod
    def load(cls, config_path: str) -> "ObservabilityPolicy":
        """Load observability tunables from YAML."""
        values = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        return cls.model_validate(values)
