"""Stage 5 tests for pricing estimates, Redis budgets, and usage persistence."""

import asyncio

import fakeredis.aioredis
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, UsageLedger
from app.limits.budget import BudgetExceeded, BudgetManager
from app.limits.pricing import PricingCatalog
from app.limits.usage_writer import UsageRecord, UsageWriter
from app.schemas.chat import ChatCompletionRequest


@pytest.mark.asyncio
async def test_budget_reserve_reconcile_release_and_monthly_keys() -> None:
    """Reservations are concurrency-safe, reconciled to actual cost, and releasable."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    manager = BudgetManager(redis_client)

    assert await manager.reserve("team-1", 0.1, 0.06) == 0.6
    with pytest.raises(BudgetExceeded):
        await manager.reserve("team-1", 0.1, 0.05)
    ratio = await manager.reconcile("team-1", 0.1, 0.06, 0.04)
    assert ratio == 0.4
    used, reserved = await manager.usage("team-1")
    assert used == 0.04
    assert reserved == 0.0

    await manager.reserve("team-1", 0.1, 0.03)
    await manager.release("team-1", 0.1, 0.03)
    used, reserved = await manager.usage("team-1")
    assert used == 0.04
    assert reserved == 0.0
    keys = await redis_client.keys("budget:team-1:*")
    assert f":{manager.month_key()}:" in keys[0]
    await redis_client.aclose()


@pytest.mark.asyncio
async def test_concurrent_budget_reservations_never_exceed_budget() -> None:
    """Parallel reservations use one Lua transaction against shared team spend."""
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    manager = BudgetManager(redis_client)
    results = await asyncio.gather(
        *(manager.reserve("parallel-team", 0.1, 0.03) for _ in range(10)),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 3
    used, reserved = await manager.usage("parallel-team")
    assert used == 0.0
    assert reserved == 0.09
    await redis_client.aclose()


def test_pricing_separates_input_output_and_estimates_tokens() -> None:
    """Pricing uses separate rates and unknown tokenizer models use heuristic counts."""
    catalog = PricingCatalog.load("config/pricing.yaml", chars_per_token=4)
    request = ChatCompletionRequest(
        model="custom-local", messages=[{"role": "user", "content": "abcdefgh"}]
    )

    assert catalog.estimate_input_tokens(request, "custom-local") == 4
    assert catalog.actual_cost("gpt-4o", 1_000_000, 500_000) == 7.5
    assert catalog.estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000) == 0.75


@pytest.mark.asyncio
async def test_usage_writer_persists_records_asynchronously() -> None:
    """The queue worker writes ledger records without request-path DB inserts."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    writer = UsageWriter(sessions, maxsize=2)
    await writer.start()
    writer.enqueue(
        UsageRecord(
            team_id="team-1",
            api_key_id=None,
            requested_model="smart",
            served_provider="openai",
            served_model="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.0001,
            latency_ms=123,
            fallback_used=False,
            status="success",
        )
    )
    await writer.close()

    async with sessions() as session:
        record = await session.scalar(select(UsageLedger))
    assert record is not None
    assert record.requested_model == "smart"
    assert record.cost_usd == 0.0001
    assert record.status == "success"
    await engine.dispose()
