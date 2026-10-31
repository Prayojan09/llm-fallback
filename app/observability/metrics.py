"""Low-cardinality Prometheus metrics for gateway traffic and spend."""

from pathlib import Path

import yaml
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from app.config import get_settings

_observability_config = yaml.safe_load(
    Path(get_settings().observability_config_path).read_text(encoding="utf-8")
)

REQUESTS = Counter(
    "gateway_requests_total",
    "Completed chat completion requests",
    ("team", "model", "provider", "status"),
)
REQUEST_DURATION = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end gateway request duration",
    ("team", "model"),
    buckets=tuple(_observability_config["request_duration_buckets_seconds"]),
)
PROVIDER_ATTEMPTS = Counter(
    "gateway_provider_attempts_total",
    "Provider attempts by outcome",
    ("provider", "model", "outcome"),
)
FALLBACKS = Counter(
    "gateway_fallbacks_total", "Successful provider fallback transitions", ("from", "to")
)
TOKENS = Counter("gateway_tokens_total", "Tokens processed", ("team", "direction"))
COST_USD = Counter("gateway_cost_usd_total", "Provider cost in USD", ("team",))
RATE_LIMITED = Counter("gateway_rate_limited_total", "Rate limited requests", ("team",))
CIRCUIT_STATE = Gauge(
    "gateway_circuit_state", "Provider breaker state (closed=0, half-open=1, open=2)", ("provider",)
)
BUDGET_USED_RATIO = Gauge(
    "gateway_budget_used_ratio", "Fraction of monthly budget consumed", ("team",)
)


def record_usage(team: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    """Record successful request tokens and cost without prompt dimensions."""
    TOKENS.labels(team, "input").inc(input_tokens)
    TOKENS.labels(team, "output").inc(output_tokens)
    COST_USD.labels(team).inc(cost_usd)


def render_metrics() -> tuple[bytes, str]:
    """Return Prometheus exposition bytes and the correct media type."""
    return generate_latest(), CONTENT_TYPE_LATEST
