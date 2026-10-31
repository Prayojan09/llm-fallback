"""Redis-atomic monthly budget reservation and reconciliation."""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.limits.token_bucket import RateLimitUnavailable

_BUDGET_SCRIPT = (Path(__file__).parent / "budget.lua").read_text(encoding="utf-8")
_RECONCILE_SCRIPT = """
local reserved = tonumber(redis.call('GET', KEYS[1]) or '0')
local used = tonumber(redis.call('GET', KEYS[2]) or '0')
local amount = tonumber(ARGV[1])
local actual = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local budget = tonumber(ARGV[4])
local next_reserved = math.max(0, reserved - amount)
redis.call('SET', KEYS[1], next_reserved, 'EX', ttl)
redis.call('INCRBY', KEYS[2], actual)
redis.call('EXPIRE', KEYS[2], ttl)
local total_used = used + actual
local ratio = budget > 0 and math.ceil(total_used / budget * 10000) or 0
return {next_reserved, total_used, ratio}
"""


class BudgetExceeded(Exception):
    """Raised when the reservation would exceed a positive monthly budget."""


class BudgetManager:
    """Manage concurrent monthly spend counters in Redis micro-USD units."""

    def __init__(self, redis: Redis) -> None:
        """Store the Redis client used for atomic monthly counters."""
        self._redis = redis

    @staticmethod
    def month_key() -> str:
        """Return the current UTC budget period key."""
        return datetime.now(UTC).strftime("%Y-%m")

    async def reserve(
        self,
        team_id: str,
        monthly_budget_usd: float,
        estimate_usd: float,
        period: str | None = None,
    ) -> float:
        """Reserve estimated cost atomically; zero budget means unlimited."""
        amount = self._units(estimate_usd)
        budget = self._units(monthly_budget_usd)
        keys = self._keys(team_id, period)
        try:
            result = await self._redis.eval(
                _BUDGET_SCRIPT, 2, *keys, amount, budget, self._ttl_to_month_end()
            )
        except RedisError as exc:
            raise RateLimitUnavailable("Budget store unavailable") from exc
        if int(result[0]) == 0:
            raise BudgetExceeded("Monthly budget would be exceeded")
        return int(result[3]) / 10_000

    async def reconcile(
        self,
        team_id: str,
        monthly_budget_usd: float,
        reserved_usd: float,
        actual_usd: float,
        period: str | None = None,
    ) -> float:
        """Move an estimate reservation to actual monthly spend atomically."""
        reserved_key, used_key = self._keys(team_id, period)
        try:
            result = await self._redis.eval(
                _RECONCILE_SCRIPT,
                2,
                reserved_key,
                used_key,
                self._units(reserved_usd),
                self._units(actual_usd),
                self._ttl_to_month_end(),
                self._units(monthly_budget_usd),
            )
        except RedisError as exc:
            raise RateLimitUnavailable("Budget store unavailable") from exc
        return int(result[2]) / 10_000

    async def release(
        self,
        team_id: str,
        monthly_budget_usd: float,
        reserved_usd: float,
        period: str | None = None,
    ) -> None:
        """Release a reservation after a failed provider call."""
        await self.reconcile(team_id, monthly_budget_usd, reserved_usd, 0.0, period)

    async def usage(self, team_id: str) -> tuple[float, float]:
        """Return current spent and reserved budget values."""
        try:
            reserved, used = await self._redis.mget(*self._keys(team_id))
        except RedisError as exc:
            raise RateLimitUnavailable("Budget store unavailable") from exc
        return self._dollars(int(used or 0)), self._dollars(int(reserved or 0))

    def _keys(self, team_id: str, period: str | None = None) -> tuple[str, str]:
        """Build period-scoped spend and reservation keys."""
        prefix = f"budget:{team_id}:{period or self.month_key()}"
        return f"{prefix}:reserved", f"{prefix}:used"

    @staticmethod
    def _ttl_to_month_end() -> int:
        """Keep monthly budget counters through the end of the UTC month."""
        now = datetime.now(UTC)
        next_month = (now.replace(day=28) + timedelta(days=4)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, math.ceil((next_month - now).total_seconds()))

    @staticmethod
    def _units(amount: float) -> int:
        """Convert dollars to rounded integer micro-USD for atomic arithmetic."""
        return max(0, math.ceil(amount * 1_000_000))

    @staticmethod
    def _dollars(units: int) -> float:
        """Convert integer micro-USD into dollars."""
        return units / 1_000_000
