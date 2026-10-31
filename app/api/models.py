"""Model discovery endpoint."""

from fastapi import APIRouter, Request

from app.schemas.models import ModelItem, ModelListResponse

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", response_model=ModelListResponse)
async def list_models(request: Request) -> ModelListResponse:
    """List configured aliases and concrete model names."""
    names = request.app.state.routes.list_models()
    return ModelListResponse(data=[ModelItem(id=name) for name in names])
