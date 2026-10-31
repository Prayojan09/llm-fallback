"""Atomic Redis circuit breaker shared by gateway instances."""

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.observability.metrics import CIRCUIT_STATE
from app.routing.policies import FallbackPolicy

_ALLOW_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
local open_until = tonumber(redis.call('HGET', KEYS[1], 'open_until') or '0')
local probes = tonumber(redis.call('HGET', KEYS[1], 'probes') or '0')
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local max_probes = tonumber(ARGV[1])
if state == 'open' then
    if now < open_until then return {0, state} end
    state = 'half_open'
    probes = 0
    redis.call('HSET', KEYS[1], 'state', state, 'probes', probes)
end
if state == 'half_open' then
    if probes >= max_probes then return {0, state} end
    probes = probes + 1
    redis.call('HSET', KEYS[1], 'probes', probes)
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return {1, state}
"""

_FAILURE_SCRIPT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local window = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local cooldown = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'half_open' then
    redis.call('HSET', KEYS[1], 'state', 'open', 'open_until', now + cooldown, 'probes', 0)
    redis.call('EXPIRE', KEYS[1], ttl)
    return {threshold, 'open'}
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - window)
redis.call('ZADD', KEYS[2], now, ARGV[5])
local failures = redis.call('ZCARD', KEYS[2])
if failures >= threshold then
    redis.call('HSET', KEYS[1], 'state', 'open', 'open_until', now + cooldown, 'probes', 0)
end
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
return {failures, redis.call('HGET', KEYS[1], 'state') or 'closed'}
"""

_SUCCESS_SCRIPT = """
local state = redis.call('HGET', KEYS[1], 'state') or 'closed'
if state == 'half_open' then
    redis.call('DEL', KEYS[1], KEYS[2])
else
    redis.call('DEL', KEYS[2])
    redis.call('HSET', KEYS[1], 'state', 'closed', 'probes', 0)
end
return 1
"""


class CircuitBreaker:
    """Coordinate provider admission and breaker transitions through Redis Lua."""

    def __init__(self, redis: Redis, policy: FallbackPolicy) -> None:
        """Store the shared Redis connection and validated thresholds."""
        self._redis = redis
        self._policy = policy
        self._ttl = policy.breaker_failure_window_seconds + policy.breaker_cooldown_seconds * 2

    async def allow(self, provider: str) -> bool:
        """Atomically permit a request or one of the limited half-open probes."""
        try:
            result = await self._redis.eval(
                _ALLOW_SCRIPT,
                1,
                self._state_key(provider),
                self._policy.breaker_half_open_probes,
                self._ttl,
            )
        except RedisError:
            if self._policy.breaker_redis_failure_policy == "open":
                return True
            raise
        state = str(result[1])
        CIRCUIT_STATE.labels(provider).set({"closed": 0, "half_open": 1, "open": 2}.get(state, -1))
        return int(result[0]) == 1

    async def record_failure(self, provider: str) -> None:
        """Atomically add a failure to its sliding window and open if needed."""
        try:
            result = await self._redis.eval(
                _FAILURE_SCRIPT,
                2,
                self._state_key(provider),
                self._failures_key(provider),
                self._policy.breaker_failure_window_seconds,
                self._policy.breaker_failure_threshold,
                self._policy.breaker_cooldown_seconds,
                self._ttl,
                self._member(),
            )
            state = str(result[1])
            CIRCUIT_STATE.labels(provider).set(
                {"closed": 0, "half_open": 1, "open": 2}.get(state, -1)
            )
        except RedisError:
            if self._policy.breaker_redis_failure_policy == "closed":
                raise

    async def record_success(self, provider: str) -> None:
        """Close a half-open breaker or clear stale failures after success."""
        try:
            await self._redis.eval(
                _SUCCESS_SCRIPT,
                2,
                self._state_key(provider),
                self._failures_key(provider),
            )
            CIRCUIT_STATE.labels(provider).set(0)
        except RedisError:
            if self._policy.breaker_redis_failure_policy == "closed":
                raise

    async def state(self, provider: str) -> str:
        """Return current breaker state for health and test inspection."""
        try:
            return str(await self._redis.hget(self._state_key(provider), "state") or "closed")
        except RedisError:
            return "unknown"

    @staticmethod
    def _member() -> str:
        """Create a unique sorted-set member for each recorded failure."""
        import uuid

        return uuid.uuid4().hex

    @staticmethod
    def _state_key(provider: str) -> str:
        """Return provider state key."""
        return f"breaker:{provider}:state"

    @staticmethod
    def _failures_key(provider: str) -> str:
        """Return provider failure-window sorted set key."""
        return f"breaker:{provider}:failures"
