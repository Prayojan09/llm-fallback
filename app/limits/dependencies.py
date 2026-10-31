"""FastAPI dependency for the configured rate limiter."""

from fastapi import Request

from app.limits.token_bucket import TokenBucketLimiter


def get_rate_limiter(request: Request) -> TokenBucketLimiter:
    """Return the limiter initialized during application startup."""
    return request.app.state.rate_limiter
