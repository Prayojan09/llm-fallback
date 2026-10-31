"""Admin endpoints for team and team API key lifecycle."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keys import api_key_prefix, generate_api_key, hash_api_key
from app.auth.team import (
    bearer_authorization,
    invalidate_key_cache,
    invalidate_team_cache,
    verify_admin_token,
)
from app.db.models import ApiKey, Team
from app.db.session import get_db
from app.schemas.teams import (
    ApiKeyCreated,
    ApiKeyMetadata,
    ApiKeyRotationRequest,
    TeamCreate,
    TeamResponse,
    TeamUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class FakeProviderAvailability(BaseModel):
    """Local demo control for deterministic FakeProvider availability."""

    model_config = ConfigDict(extra="forbid")

    available: bool


async def require_admin(
    request: Request, authorization: str | None = Depends(bearer_authorization)
) -> None:
    """Authorize a request with the separate ADMIN_TOKEN."""
    verify_admin_token(request, authorization)


@router.put("/fake-provider/{provider_name}", response_model=FakeProviderAvailability)
async def set_fake_provider_availability(
    provider_name: str,
    body: FakeProviderAvailability,
    request: Request,
    _: None = Depends(require_admin),
) -> FakeProviderAvailability:
    """Toggle a named fake provider for local failover demonstrations."""
    if request.app.state.settings.environment.lower() == "production":
        raise HTTPException(status_code=404, detail="Not found")
    from app.providers.fake import FakeProvider

    provider = request.app.state.providers.get(provider_name)
    if not isinstance(provider, FakeProvider):
        raise HTTPException(status_code=404, detail="Fake provider not found")
    provider.set_available(body.available)
    return body


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreate,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> TeamResponse:
    """Create a team with its budget, limits, and model allow-list."""
    team = Team(**body.model_dump())
    session.add(team)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A team with that name already exists") from exc
    await session.refresh(team)
    return TeamResponse.model_validate(team, from_attributes=True)


@router.get("/teams", response_model=list[TeamResponse])
async def list_teams(
    session: AsyncSession = Depends(get_db), _: None = Depends(require_admin)
) -> list[TeamResponse]:
    """List teams without exposing any API key hashes or secrets."""
    result = await session.execute(select(Team).order_by(Team.name))
    return [TeamResponse.model_validate(team, from_attributes=True) for team in result.scalars()]


@router.patch("/teams/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    body: TeamUpdate,
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> TeamResponse:
    """Update a team's configuration and invalidate cached auth snapshots."""
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(team, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A team with that name already exists") from exc
    await session.refresh(team)
    await invalidate_team_cache(request, session, team_id)
    return TeamResponse.model_validate(team, from_attributes=True)


@router.post(
    "/teams/{team_id}/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED
)
async def create_team_key(
    team_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> ApiKeyCreated:
    """Create a team API key and return the plaintext secret exactly once."""
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    api_key = generate_api_key()
    key = ApiKey(team_id=team_id, key_hash=hash_api_key(api_key), prefix=api_key_prefix(api_key))
    session.add(key)
    await session.commit()
    await session.refresh(key)
    await invalidate_key_cache(request, key.key_hash)
    metadata = ApiKeyMetadata.model_validate(key, from_attributes=True).model_dump()
    return ApiKeyCreated(**metadata, api_key=api_key)


@router.post(
    "/teams/{team_id}/keys/rotate",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_team_key(
    team_id: str,
    body: ApiKeyRotationRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> ApiKeyCreated:
    """Create a replacement key and optionally revoke the old key atomically."""
    if await session.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    old_key: ApiKey | None = None
    if body.revoke_key_id is not None:
        old_key = await session.get(ApiKey, body.revoke_key_id)
        if old_key is None or old_key.team_id != team_id:
            raise HTTPException(status_code=404, detail="API key not found for team")
        if old_key.revoked_at is not None:
            raise HTTPException(status_code=409, detail="API key is already revoked")

    plaintext_key = generate_api_key()
    new_key = ApiKey(
        team_id=team_id,
        key_hash=hash_api_key(plaintext_key),
        prefix=api_key_prefix(plaintext_key),
    )
    session.add(new_key)
    if old_key is not None:
        old_key.revoked_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(new_key)
    if old_key is not None:
        await invalidate_key_cache(request, old_key.key_hash)
    metadata = ApiKeyMetadata.model_validate(new_key, from_attributes=True).model_dump()
    return ApiKeyCreated(**metadata, api_key=plaintext_key)


@router.get("/teams/{team_id}/keys", response_model=list[ApiKeyMetadata])
async def list_team_keys(
    team_id: str,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> list[ApiKeyMetadata]:
    """List safe key metadata for a team."""
    if await session.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")
    result = await session.execute(
        select(ApiKey).where(ApiKey.team_id == team_id).order_by(ApiKey.created_at)
    )
    return [ApiKeyMetadata.model_validate(key, from_attributes=True) for key in result.scalars()]


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_team_key(
    key_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
) -> Response:
    """Revoke a key and immediately remove its cached authorization."""
    key = await session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        await session.commit()
        await invalidate_key_cache(request, key.key_hash)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
