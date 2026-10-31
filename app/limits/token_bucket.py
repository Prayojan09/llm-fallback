"""Atomic two-dimensional Redis token bucket limiter."""

import math
from dataclasses import dataclass
from pathlib import Path

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.limits.policies import RateLimitPolicy
from app.schemas.chat import ChatCompletionRequest
from app.schemas.teams import TeamAuth

_SCRIPT_PATH = Path(__file__).with_name("lua") / "token_bucket.lua"
TOKEN_BUCKET_SCRIPT = _SCRIPT_PATH.read_text(encoding="utf-8")


@dataclass(frozen=True)
class RateLimitResult:
    """Decision and rate-limit metadata for one gateway request."""

    allowed: bool
    request_remaining: int | None = None
    token_remaining: int | None = None
    request_reset: int | None = None
    token_reset: int | None = None
    retry_after: int | None = None
    redis_available: bool = True

    def response_headers(self, team: TeamAuth) -> dict[str, str]:
        """Build standard per-dimension rate limit response headers."""
        if not self.redis_available:
            return {"X-Gateway-RateLimit-Status": "unavailable"}
        return {
            "X-RateLimit-Limit-Requests": str(team.rpm_limit),
            "X-RateLimit-Remaining-Requests": str(self.request_remaining),
            "X-RateLimit-Reset-Requests": str(self.request_reset),
            "X-RateLimit-Limit-Tokens": str(team.tpm_limit),
            "X-RateLimit-Remaining-Tokens": str(self.token_remaining),
            "X-RateLimit-Reset-Tokens": str(self.token_reset),
        }


class RateLimitUnavailable(Exception):
    """Raised when Redis is unavailable and policy is fail-closed."""


class RateLimitExceeded(Exception):
    """Raised when either the request or token bucket is exhausted."""

    def __init__(self, retry_after: int) -> None:
        """Record the minimum wait suggested by Redis."""
        super().__init__("Rate limit exceeded")
        self.retry_after = max(1, retry_after)


class TokenBucketLimiter:
    """Enforce request and estimated-token limits using one Redis Lua call."""

    def __init__(self, redis_client: Redis, policy: RateLimitPolicy) -> None:
        """Store the async Redis client and validated tunable policy."""
        self._redis = redis_client
        self._policy = policy

    def estimate_tokens(self, request: ChatCompletionRequest) -> int:
        """Estimate request plus reserved completion tokens for this stage."""
        input_characters = sum(len(message.content) for message in request.messages)
        input_tokens = math.ceil(input_characters / self._policy.characters_per_token_estimate)
        output_tokens = request.max_tokens or self._policy.default_completion_tokens
        return max(1, input_tokens + output_tokens)

    async def consume(self, team: TeamAuth, token_cost: int) -> RateLimitResult:
        """Atomically check and debit a team's request and token buckets."""
        request_rate = team.rpm_limit / 60
        token_rate = team.tpm_limit / 60
        request_capacity = team.rpm_burst_capacity or math.ceil(
            team.rpm_limit * self._policy.request_burst_multiplier
        )
        token_capacity = team.tpm_burst_capacity or math.ceil(
            team.tpm_limit * self._policy.token_burst_multiplier
        )
        token_capacity = max(token_capacity, 1)
        if token_cost > token_capacity:
            raise RateLimitExceeded(math.ceil((token_cost - token_capacity) / token_rate))
        refill_seconds = max(request_capacity / request_rate, token_capacity / token_rate)
        ttl_ms = max(
            self._policy.minimum_key_ttl_seconds * 1000,
            math.ceil(refill_seconds * 2 * 1000),
        )
        try:
            result = await self._redis.eval(
                TOKEN_BUCKET_SCRIPT,
                2,
                f"rate:{team.id}:requests",
                f"rate:{team.id}:tokens",
                request_rate,
                request_capacity,
                token_rate,
                token_capacity,
                token_cost,
                ttl_ms,
            )
        except RedisError as exc:
            if self._policy.redis_failure_policy == "open":
                return RateLimitResult(allowed=True, redis_available=False)
            raise RateLimitUnavailable from exc
        allowed, request_left, tokens_left, request_reset, token_reset, retry_after = map(
            int, result
        )
        return RateLimitResult(
            allowed=bool(allowed),
            request_remaining=request_left,
            token_remaining=tokens_left,
            request_reset=request_reset,
            token_reset=token_reset,
            retry_after=retry_after,
        )
