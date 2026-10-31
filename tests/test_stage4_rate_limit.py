"""Rate limiting tests against fakeredis and the production Lua script."""

import asyncio

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError

from app.auth.team import authenticate_team
from app.config import Settings
from app.limits.dependencies import get_rate_limiter
from app.limits.policies import RateLimitPolicy
from app.limits.token_bucket import (
    RateLimitResult,
    RateLimitUnavailable,
    TokenBucketLimiter,
)
from app.main import create_app
from app.schemas.chat import ChatCompletionRequest
from app.schemas.teams import TeamAuth


def make_policy(failure_policy: str = "closed") -> RateLimitPolicy:
    """Build a small deterministic policy for bucket tests."""
    return RateLimitPolicy(
        request_burst_multiplier=1,
        token_burst_multiplier=1,
        default_completion_tokens=10,
        characters_per_token_estimate=4,
        redis_failure_policy=failure_policy,
        minimum_key_ttl_seconds=60,
        budget_warn_ratio=0.8,
        usage_queue_size=100,
    )


def make_team() -> TeamAuth:
    """Build a team with independently controlled bucket capacities."""
    return TeamAuth(
        id="bucket-team",
        name="Bucket Team",
        monthly_budget_usd=10,
        rpm_limit=5,
        tpm_limit=60,
        rpm_burst_capacity=5,
        tpm_burst_capacity=60,
        allowed_models=["*"],
        active=True,
    )


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_exceed_atomic_request_bucket() -> None:
    """Only the configured request burst succeeds for a concurrent burst."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = TokenBucketLimiter(redis_client, make_policy())

    results = await asyncio.gather(*(limiter.consume(make_team(), 1) for _ in range(30)))

    assert sum(result.allowed for result in results) == 5
    assert all(result.request_remaining is not None for result in results)
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_buckets_debit_together_or_neither() -> None:
    """A token rejection does not consume a request token in the other bucket."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = TokenBucketLimiter(redis_client, make_policy())
    team = make_team().model_copy(update={"rpm_limit": 2, "rpm_burst_capacity": 2})

    assert (await limiter.consume(team, 59)).allowed
    denied = await limiter.consume(team, 2)
    assert not denied.allowed
    assert (await limiter.consume(team, 1)).allowed
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_redis_failure_policy_is_configurable() -> None:
    """Fail-open permits the request while fail-closed reports service failure."""

    class BrokenRedis:
        """Redis stub that simulates a connection outage."""

        async def eval(self, *args: object) -> list[int]:
            """Raise a Redis transport failure for every script invocation."""
            raise ConnectionError("Redis unavailable")

    team = make_team()
    opened = await TokenBucketLimiter(BrokenRedis(), make_policy("open")).consume(team, 1)
    assert opened.allowed and not opened.redis_available

    with pytest.raises(RateLimitUnavailable):
        await TokenBucketLimiter(BrokenRedis(), make_policy("closed")).consume(team, 1)


def test_token_estimate_uses_configurable_values() -> None:
    """The stage 4 estimate uses configured character and completion estimates."""
    limiter = TokenBucketLimiter(object(), make_policy())
    request = ChatCompletionRequest(
        messages=[{"role": "user", "content": "12345678"}], max_tokens=20
    )

    assert limiter.estimate_tokens(request) == 22


def test_rate_limited_response_uses_openai_error_and_headers() -> None:
    """The endpoint returns a structured 429 plus retry and remaining headers."""

    class DenyLimiter:
        """A deterministic limiter stub that rejects one request."""

        def estimate_tokens(self, request: ChatCompletionRequest) -> int:
            """Return a small estimate for the API test."""
            return 1

        async def consume(self, team: TeamAuth, token_cost: int) -> RateLimitResult:
            """Return a depleted bucket decision."""
            return RateLimitResult(
                allowed=False,
                request_remaining=0,
                token_remaining=4,
                request_reset=12,
                token_reset=8,
                retry_after=12,
            )

    app = create_app(Settings(provider="fake"))
    app.dependency_overrides[authenticate_team] = lambda: make_team()
    app.dependency_overrides[get_rate_limiter] = lambda: DenyLimiter()
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "fake-model", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"
    assert response.headers["retry-after"] == "12"
    assert response.headers["x-ratelimit-remaining-requests"] == "0"
    assert response.headers["x-ratelimit-reset-tokens"] == "8"
