"""Routing, fallback, and breaker behavior using only FakeProvider and fakeredis."""

import asyncio

import fakeredis.aioredis
import pytest

from app.providers.errors import BadRequest, ServerError
from app.providers.fake import FakeProvider
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.policies import FallbackPolicy
from app.routing.router import AllProvidersFailed, ProviderRouter
from app.routing.routes import ProviderTarget
from app.schemas.chat import ChatCompletionRequest


def make_policy(**overrides: object) -> FallbackPolicy:
    """Create a fast deterministic routing policy for tests."""
    values: dict[str, object] = {
        "retries_per_provider": 0,
        "retry_base_seconds": 0,
        "retry_max_seconds": 0.1,
        "retry_jitter_seconds": 0,
        "attempt_timeout_seconds": 1,
        "overall_timeout_seconds": 2,
        "breaker_failure_threshold": 5,
        "breaker_failure_window_seconds": 30,
        "breaker_cooldown_seconds": 1,
        "breaker_half_open_probes": 1,
        "breaker_redis_failure_policy": "open",
    }
    values.update(overrides)
    return FallbackPolicy.model_validate(values)


def make_request() -> ChatCompletionRequest:
    """Create a minimal normalized request."""
    return ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}])


@pytest.mark.asyncio
async def test_primary_server_error_falls_back_and_reports_metadata() -> None:
    """A transient primary failure uses the next configured target."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    policy = make_policy()
    breaker = CircuitBreaker(redis_client, policy)
    providers = {
        "primary": FakeProvider(failures=[ServerError("temporary failure")]),
        "secondary": FakeProvider(),
    }
    router = ProviderRouter(providers, breaker, policy)

    result = await router.chat(
        make_request(),
        [ProviderTarget("primary", "primary-model"), ProviderTarget("secondary", "backup-model")],
    )

    assert result.provider == "secondary"
    assert result.model == "backup-model"
    assert result.attempts == 2
    assert result.fallback_used is True
    assert await breaker.state("primary") == "closed"
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_all_transient_failures_return_clean_routing_error() -> None:
    """The router reports exhaustion after attempting every configured target."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    policy = make_policy()
    router = ProviderRouter(
        {
            "one": FakeProvider(failures=[ServerError("one down")]),
            "two": FakeProvider(failures=[ServerError("two down")]),
        },
        CircuitBreaker(redis_client, policy),
        policy,
    )

    with pytest.raises(AllProvidersFailed):
        await router.chat(
            make_request(), [ProviderTarget("one", "m1"), ProviderTarget("two", "m2")]
        )
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_bad_request_does_not_fall_back() -> None:
    """Caller-style 400 errors stop immediately and leave the secondary untouched."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    policy = make_policy(retries_per_provider=2)
    secondary_calls = 0

    class CountingProvider(FakeProvider):
        """Track if an otherwise healthy fallback provider was called."""

        async def chat(self, request: ChatCompletionRequest):
            nonlocal secondary_calls
            secondary_calls += 1
            return await super().chat(request)

    router = ProviderRouter(
        {
            "primary": FakeProvider(failures=[BadRequest("invalid request")]),
            "secondary": CountingProvider(),
        },
        CircuitBreaker(redis_client, policy),
        policy,
    )

    with pytest.raises(BadRequest):
        await router.chat(
            make_request(), [ProviderTarget("primary", "m1"), ProviderTarget("secondary", "m2")]
        )
    assert secondary_calls == 0
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_breaker_opens_skips_and_recovers_after_half_open_probe() -> None:
    """Breaker state is shared in Redis and a successful probe closes it."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    policy = make_policy(breaker_failure_threshold=2, breaker_cooldown_seconds=1)
    breaker = CircuitBreaker(redis_client, policy)

    await breaker.record_failure("provider")
    await breaker.record_failure("provider")
    assert await breaker.state("provider") == "open"
    assert await breaker.allow("provider") is False

    await asyncio.sleep(1.05)
    assert await breaker.allow("provider") is True
    assert await breaker.state("provider") == "half_open"
    await breaker.record_success("provider")
    assert await breaker.state("provider") == "closed"
    assert await breaker.allow("provider") is True
    await redis_client.aclose()
