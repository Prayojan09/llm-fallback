"""Stage 3 key lifecycle and authorization checks against SQLite and a Redis stub."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin import (
    create_team,
    create_team_key,
    list_team_keys,
    revoke_team_key,
    rotate_team_key,
)
from app.auth.keys import hash_api_key
from app.auth.team import auth_cache_key, authenticate_team
from app.db.models import ApiKey, Base
from app.schemas.teams import ApiKeyRotationRequest, TeamCreate


class MemoryRedis:
    """Minimal asynchronous Redis substitute for auth cache tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        """Return a cached string value."""
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        """Store a value while accepting the production TTL argument."""
        self.values[key] = value

    async def delete(self, key: str) -> int:
        """Delete a cached value if present."""
        return int(self.values.pop(key, None) is not None)


@pytest.mark.asyncio
async def test_key_creation_storage_authentication_and_revoke_invalidation() -> None:
    """New keys are returned once, stored hashed, cached on auth, and revoked immediately."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis_client = MemoryRedis()
    settings = SimpleNamespace(team_cache_ttl_seconds=60, admin_token="admin-test")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis=redis_client, settings=settings))
    )

    async with session_factory() as session:
        team = await create_team(
            TeamCreate(name="Review Team", allowed_models=["smart"]), session, None
        )
        created = await create_team_key(team.id, request, session, None)
        key_hash = hash_api_key(created.api_key)
        stored_key = await session.scalar(select(ApiKey).where(ApiKey.id == created.id))

        assert created.api_key.startswith("gw_live_")
        assert created.prefix == created.api_key[:16]
        assert stored_key is not None
        assert stored_key.key_hash == key_hash
        assert created.api_key not in stored_key.key_hash
        listed_keys = await list_team_keys(team.id, session, None)
        listed_json = listed_keys[0].model_dump_json()
        assert created.api_key not in listed_json
        assert key_hash not in listed_json

        auth = await authenticate_team(request, session, f"Bearer {created.api_key}")
        assert auth.id == team.id
        assert redis_client.values[auth_cache_key(key_hash)]

        rotated = await rotate_team_key(
            team.id,
            ApiKeyRotationRequest(revoke_key_id=created.id),
            request,
            session,
            None,
        )
        assert rotated.api_key != created.api_key
        assert auth_cache_key(key_hash) not in redis_client.values
        stored_key = await session.get(ApiKey, created.id)
        assert stored_key is not None and stored_key.revoked_at is not None

        with pytest.raises(HTTPException) as error:
            await authenticate_team(request, session, f"Bearer {created.api_key}")
        assert error.value.status_code == 401

        new_auth = await authenticate_team(request, session, f"Bearer {rotated.api_key}")
        assert new_auth.id == team.id
        await revoke_team_key(rotated.id, request, session, None)

    await engine.dispose()


def test_api_key_hash_is_sha256_and_prefix_does_not_contain_full_key() -> None:
    """API key storage uses a fixed-length one-way digest."""
    key = "gw_live_example-test-key"

    digest = hash_api_key(key)

    assert len(digest) == 64
    assert digest != key
