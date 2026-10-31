"""OpenAI-style API error response models."""

from pydantic import BaseModel


class OpenAIError(BaseModel):
    """One normalized API error."""

    message: str
    type: str
    code: str


class OpenAIErrorResponse(BaseModel):
    """OpenAI-compatible error envelope."""

    error: OpenAIError
