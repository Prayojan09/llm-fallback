"""Streaming API and provider fallback tests without real upstream calls."""

import fakeredis.aioredis
import pytest

from app.providers.errors import ServerError
from app.providers.fake import FakeProvider
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.policies import FallbackPolicy
from app.routing.router import ProviderRouter
from app.routing.routes import ProviderTarget
from app.schemas.chat import ChatCompletionRequest


@pytest.mark.asyncio
async def test_stream_falls_back_before_first_text_delta() -> None:
    """A transient failure before output starts may safely use the next provider."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    policy = FallbackPolicy.load("config/fallback.yaml")
    policy = policy.model_copy(
        update={
            "retries_per_provider": 0,
            "retry_base_seconds": 0,
            "retry_jitter_seconds": 0,
            "attempt_timeout_seconds": 1,
            "overall_timeout_seconds": 2,
        }
    )
    router = ProviderRouter(
        {
            "primary": FakeProvider(failures=[ServerError("temporary failure")]),
            "secondary": FakeProvider(),
        },
        CircuitBreaker(redis_client, policy),
        policy,
    )
    stream = router.stream(
        ChatCompletionRequest(messages=[{"role": "user", "content": "Hi"}], stream=True),
        [ProviderTarget("primary", "primary-model"), ProviderTarget("secondary", "backup-model")],
    )

    first = await anext(stream)
    assert first == ("secondary", "backup-model", "Fake ", True, 2)
    await stream.aclose()
    await redis_client.aclose()
