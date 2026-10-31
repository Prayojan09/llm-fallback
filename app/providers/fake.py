"""Deterministic provider for local development and tests."""

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import cast

from app.providers.errors import ProviderError, ServerError
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Usage,
)


class FakeProvider:
    """Return predictable completions without making network requests."""

    def __init__(
        self, failures: list[ProviderError] | None = None, latency_seconds: float = 0.0
    ) -> None:
        """Set deterministic one-shot failures and optional response latency."""
        self._failures = deque(failures or [])
        self._latency_seconds = latency_seconds
        self._available = True

    def set_available(self, available: bool) -> None:
        """Set availability for deterministic local failover demonstrations."""
        self._available = available

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Echo the latest user message in a normalized completion."""
        if self._latency_seconds:
            await asyncio.sleep(self._latency_seconds)
        if not self._available:
            raise ServerError("FakeProvider is configured as unavailable")
        if self._failures:
            raise cast(ProviderError, self._failures.popleft())
        user_message = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "Hello from FakeProvider.",
        )
        content = f"Fake response: {user_message}"
        prompt_tokens = sum(len(message.content.split()) for message in request.messages)
        completion_tokens = len(content.split())
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=request.model or "fake-model",
            choices=[ChatCompletionChoice(message=ChatMessage(role="assistant", content=content))],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield response text in chunks for the provider interface."""
        response = await self.chat(request)
        content = response.choices[0].message.content
        for word in content.split(" "):
            yield f"{word} "

    async def health(self) -> bool:
        """Report that the deterministic fake is always available."""
        return True
