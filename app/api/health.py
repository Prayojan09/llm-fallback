"""Health and readiness endpoints."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Simple service health response."""

    status: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report that the process is alive."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready(request: Request) -> HealthResponse:
    """Report whether startup resources have been initialized."""
    if getattr(request.app.state, "http_client", None) is None:
        return HealthResponse(status="not_ready")
    return HealthResponse(status="ready")
