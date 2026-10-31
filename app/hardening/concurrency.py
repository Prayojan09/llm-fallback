"""Atomic Redis leases for provider-wide concurrency caps."""

import asyncio
import time
import uuid

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.hardening.policies import HardeningPolicy

_ACQUIRE_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local lease_seconds = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
redis.call('ZADD', KEYS[1], now + lease_seconds, ARGV[3])
redis.call('EXPIRE', KEYS[1], math.ceil(lease_seconds))
return 1
"""
_RELEASE_SCRIPT = "return redis.call('ZREM', KEYS[1], ARGV[1])"


class ProviderConcurrencyLimiter:
    """Coordinate in-flight upstream calls across all gateway replicas."""

    def __init__(self, redis: Redis, policy: HardeningPolicy) -> None:
        """Keep Redis and provider-specific cap settings."""
        self._redis = redis
        self._policy = policy

    async def acquire(self, provider: str) -> str | None:
        """Wait up to the configured interval for a provider lease."""
        lease_id = uuid.uuid4().hex
        limit = self._policy.provider_concurrency_limits.get(
            provider, self._policy.provider_concurrency_default
        )
        deadline = time.monotonic() + self._policy.provider_concurrency_wait_seconds
        key = f"provider:concurrency:{provider}"
        while True:
            try:
                acquired = await self._redis.eval(
                    _ACQUIRE_SCRIPT,
                    1,
                    key,
                    self._policy.provider_concurrency_lease_seconds,
                    limit,
                    lease_id,
                )
            except RedisError:
                if self._policy.provider_concurrency_redis_failure_policy == "open":
                    return "untracked"
                raise
            if int(acquired) == 1:
                return lease_id
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(remaining, self._policy.provider_concurrency_poll_seconds))

    async def release(self, provider: str, lease_id: str) -> None:
        """Remove the provider lease atomically after an attempt finishes."""
        if lease_id == "untracked":
            return
        try:
            await self._redis.eval(_RELEASE_SCRIPT, 1, f"provider:concurrency:{provider}", lease_id)
        except RedisError:
            if self._policy.provider_concurrency_redis_failure_policy == "closed":
                raise
