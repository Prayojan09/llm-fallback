"""Bearer key verification and Redis-cached team lookup."""

import json
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keys import hash_api_key
from app.db.models import ApiKey, Team
from app.db.session import get_db
from app.observability.tracing import get_tracer
from app.schemas.teams import TeamAuth

_bearer_scheme = HTTPBearer(auto_error=False)


async def bearer_authorization(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str | None:
    """Expose bearer authentication as an OpenAPI security scheme for Swagger UI."""
    if credentials is None:
        return None
    return f"{credentials.scheme} {credentials.credentials}"


def auth_cache_key(key_hash: str) -> str:
    """Build a Redis key from the already-hashed API key."""
    return f"auth:team-key:{key_hash}"


async def authenticate_team(
    request: Request,
    session: AsyncSession = Depends(get_db),
    authorization: str | None = Depends(bearer_authorization),
) -> TeamAuth:
    """Authenticate a bearer API key and return its active team."""
    with get_tracer().start_as_current_span("auth"):
        return await _authenticate_team_impl(request, session, authorization)


async def _authenticate_team_impl(
    request: Request, session: AsyncSession, authorization: str | None
) -> TeamAuth:
    """Resolve the bearer key through Redis cache and the persistent key store."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Bearer API key required")
    key_hash = hash_api_key(token)
    redis_client = request.app.state.redis
    cache_key = auth_cache_key(key_hash)
    try:
        with get_tracer().start_as_current_span("auth.cache_lookup"):
            cached = await redis_client.get(cache_key)
    except RedisError:
        cached = None
    if cached:
        try:
            team = TeamAuth.model_validate_json(cached)
        except ValueError:
            await invalidate_key_cache(request, key_hash)
        else:
            if team.active:
                return team
            raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    with get_tracer().start_as_current_span("auth.database_lookup"):
        record = await session.execute(
            select(Team, ApiKey.id)
            .join(ApiKey, ApiKey.team_id == Team.id)
            .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        )
    row = record.one_or_none()
    if row is None or not row[0].active:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    team = TeamAuth.model_validate(row[0], from_attributes=True).model_copy(
        update={"api_key_id": row[1]}
    )
    try:
        await redis_client.set(
            cache_key,
            json.dumps(team.model_dump(mode="json")),
            ex=request.app.state.settings.team_cache_ttl_seconds,
        )
    except RedisError:
        pass
    return team


async def invalidate_key_cache(request: Request, key_hash: str) -> None:
    """Remove one cached key authorization after a key or team update."""
    try:
        await request.app.state.redis.delete(auth_cache_key(key_hash))
    except RedisError:
        pass


async def invalidate_team_cache(request: Request, session: AsyncSession, team_id: str) -> None:
    """Invalidate cached team snapshots for all of that team's keys."""
    result = await session.execute(select(ApiKey.key_hash).where(ApiKey.team_id == team_id))
    for key_hash in result.scalars():
        await invalidate_key_cache(request, key_hash)


def verify_admin_token(request: Request, authorization: str | None) -> None:
    """Require the configured separate admin bearer token."""
    expected = request.app.state.settings.admin_token
    scheme, _, token = (authorization or "").partition(" ")
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API is not configured")
    if scheme.lower() != "bearer" or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")
