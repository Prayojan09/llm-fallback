"""OpenAI-compatible models list schema."""

from typing import Literal

from pydantic import BaseModel


class ModelItem(BaseModel):
    """One model name exposed to clients."""

    id: str
    object: Literal["model"] = "model"
    owned_by: str = "llm-gateway"


class ModelListResponse(BaseModel):
    """OpenAI-compatible models list response."""

    object: Literal["list"] = "list"
    data: list[ModelItem]
