"""Provider interface and normalized stream chunk type."""

from collections.abc import AsyncIterator
from typing import Protocol

from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse


class Provider(Protocol):
    """Interface implemented by every upstream model provider."""

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Generate a complete response for a chat request."""
        ...

    def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield normalized text chunks for a streaming chat request."""
        ...

    async def health(self) -> bool:
        """Return whether the provider is currently usable."""
        ...
