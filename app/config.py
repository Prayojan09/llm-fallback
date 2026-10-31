"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the gateway."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LLM Gateway"
    environment: str = "local"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    provider: str = "fake"
    default_model: str = "fake-model"
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    log_level: str = "INFO"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_api_key_fallbacks: str = Field(default="", repr=False)
    openrouter_api_key: str | None = Field(default=None, repr=False)
    openrouter_api_key_fallbacks: str = Field(default="", repr=False)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    anthropic_api_key_fallbacks: str = Field(default="", repr=False)
    ollama_base_url: str = "http://localhost:11434"
    routes_config_path: str = "config/routes.yaml"
    anthropic_default_max_tokens: int = Field(default=1024, gt=0)
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = ""
    admin_token: str | None = Field(default=None, repr=False)
    team_cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limits_config_path: str = "config/rate_limits.yaml"
    pricing_config_path: str = "config/pricing.yaml"
    fallback_config_path: str = "config/fallback.yaml"
    otel_exporter_otlp_endpoint: str | None = None
    observability_config_path: str = "config/observability.yaml"
    hardening_config_path: str = "config/hardening.yaml"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
