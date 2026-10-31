"""Exact-match Redis response cache for opted-in teams."""

import hashlib
import json

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.routing.routes import ProviderTarget
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse


class CachedCompletion(BaseModel):
    """Cached completion plus safe serving metadata."""

    response: ChatCompletionResponse
    provider: str
    model: str
    fallback_used: bool


class ResponseCache:
    """Read and populate a team-isolated, TTL-bound response cache."""

    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        """Store Redis and the configured entry lifetime."""
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def key(team_id: str, request: ChatCompletionRequest, chain: list[ProviderTarget]) -> str:
        """Hash normalized request and route targets without storing prompts in keys."""
        payload = {
            "team_id": team_id,
            "request": request.model_dump(exclude_none=True, mode="json"),
            "chain": [{"provider": target.provider, "model": target.model} for target in chain],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"response-cache:{team_id}:{digest}"

    async def get(self, key: str) -> CachedCompletion | None:
        """Return a validated cached completion, ignoring cache-store failures."""
        try:
            value = await self._redis.get(key)
        except RedisError:
            return None
        if value is None:
            return None
        try:
            return CachedCompletion.model_validate_json(value)
        except ValidationError:
            try:
                await self._redis.delete(key)
            except RedisError:
                pass
            return None

    async def set(self, key: str, value: CachedCompletion) -> None:
        """Store a successful response for the configured TTL; cache failures are nonfatal."""
        try:
            await self._redis.set(key, value.model_dump_json(), ex=self._ttl_seconds)
        except RedisError:
            return
