"""Stage 9 cache and distributed provider-cap behavior."""

import asyncio

import fakeredis.aioredis
import httpx
import pytest
from fastapi.testclient import TestClient

from app.cache.response import ResponseCache
from app.config import Settings
from app.hardening.concurrency import ProviderConcurrencyLimiter
from app.hardening.policies import HardeningPolicy
from app.observability.middleware import RequestTracker
from app.providers.openai import OpenAIProvider
from app.schemas.chat import ChatCompletionRequest
from tests.test_stage1_api import create_test_app


@pytest.mark.asyncio
async def test_redis_provider_lease_enforces_configured_concurrency_limit() -> None:
    """Concurrent callers cannot both acquire a single provider slot."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    policy = HardeningPolicy.load("config/hardening.yaml").model_copy(
        update={
            "provider_concurrency_limits": {"fake": 1},
            "provider_concurrency_wait_seconds": 0,
        }
    )
    limiter = ProviderConcurrencyLimiter(redis_client, policy)

    first = await limiter.acquire("fake")
    second = await limiter.acquire("fake")
    assert first is not None
    assert second is None
    await limiter.release("fake", first)
    next_lease = await limiter.acquire("fake")
    assert next_lease is not None
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_request_tracker_drains_inflight_work_and_stops_admission() -> None:
    """Shutdown waits for active work and rejects new entries once draining starts."""
    tracker = RequestTracker()
    assert await tracker.enter()
    drain_task = asyncio.create_task(tracker.drain(1))
    await asyncio.sleep(0)
    assert not await tracker.enter()
    await tracker.leave()
    assert await drain_task


def test_opted_in_zero_temperature_requests_use_team_scoped_cache() -> None:
    """Only a team's repeated normalized temperature-zero request is served from cache."""
    application = create_test_app(cache_enabled=True)
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with TestClient(application) as client:
        application.state.response_cache = ResponseCache(redis_client, 30)
        payload = {
            "model": "fake-model",
            "temperature": 0,
            "messages": [{"role": "user", "content": "same request"}],
        }
        first = client.post("/v1/chat/completions", json=payload)
        second = client.post("/v1/chat/completions", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.headers["X-Gateway-Cache"] == "MISS"
    assert second.headers["X-Gateway-Cache"] == "HIT"
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["X-Gateway-Attempts"] == "0"


@pytest.mark.asyncio
async def test_provider_key_ring_uses_rotated_key_after_auth_rejection() -> None:
    """Provider adapters can overlap old and new environment keys during rotation."""
    seen_authorization: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        authorization = request.headers["authorization"]
        seen_authorization.append(authorization)
        if authorization == "Bearer retired-secret":
            return httpx.Response(401, json={"error": "invalid key"})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "created": 1,
                "model": "gpt-test",
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    settings = Settings(openai_api_key="retired-secret", openai_api_key_fallbacks="active-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await OpenAIProvider(client, settings).chat(
            ChatCompletionRequest(messages=[{"role": "user", "content": "test"}])
        )

    assert result.choices[0].message.content == "ok"
    assert seen_authorization == ["Bearer retired-secret", "Bearer active-secret"]
