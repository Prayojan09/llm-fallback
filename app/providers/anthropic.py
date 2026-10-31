"""Anthropic Messages API adapter."""

import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from app.config import Settings
from app.providers.errors import AuthError, ServerError, Timeout
from app.providers.http import post_json, raise_provider_error
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Usage,
)


class AnthropicProvider:
    """Translate OpenAI-shaped messages to and from Anthropic's API."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        """Store the shared client, secret, and configured token default."""
        self._client = client
        self._api_keys = self._configured_keys(
            settings.anthropic_api_key, settings.anthropic_api_key_fallbacks
        )
        self._default_max_tokens = settings.anthropic_default_max_tokens

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Generate a response with Anthropic and normalize usage and finish reason."""
        if not self._api_keys:
            raise AuthError("Anthropic API key is not configured")
        system = "\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        payload: dict[str, object] = {
            "model": request.model,
            "max_tokens": request.max_tokens or self._default_max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stop is not None:
            payload["stop_sequences"] = (
                [request.stop] if isinstance(request.stop, str) else request.stop
            )
        result: dict[str, object] | None = None
        last_auth_error: AuthError | None = None
        for api_key in self._api_keys:
            try:
                result = await post_json(
                    self._client,
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                    payload=payload,
                )
            except AuthError as exc:
                last_auth_error = exc
            else:
                break
        if result is None:
            raise last_auth_error or AuthError("Anthropic credentials were rejected")
        content_data = result.get("content", [])
        text = (
            "".join(
                str(block.get("text", ""))
                for block in content_data
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if isinstance(content_data, list)
            else ""
        )
        usage_data = result.get("usage", {})
        usage_data = usage_data if isinstance(usage_data, dict) else {}
        input_tokens = int(usage_data.get("input_tokens", 0))
        output_tokens = int(usage_data.get("output_tokens", 0))
        reason = result.get("stop_reason")
        finish_reason = (
            "length" if reason == "max_tokens" else "tool_calls" if reason == "tool_use" else "stop"
        )
        return ChatCompletionResponse(
            id=str(result.get("id", f"chatcmpl-{uuid.uuid4().hex}")),
            created=int(time.time()),
            model=str(result.get("model", request.model or "unknown")),
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=text), finish_reason=finish_reason
                )
            ],
            usage=Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield text deltas from Anthropic's server-sent event response."""
        if not self._api_keys:
            raise AuthError("Anthropic API key is not configured")
        system = "\n".join(m.content for m in request.messages if m.role == "system")
        messages = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]
        payload: dict[str, object] = {
            "model": request.model,
            "max_tokens": request.max_tokens or self._default_max_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stop is not None:
            payload["stop_sequences"] = (
                [request.stop] if isinstance(request.stop, str) else request.stop
            )
        last_auth_error: AuthError | None = None
        try:
            for api_key in self._api_keys:
                sent_text = False
                try:
                    async with self._client.stream(
                        "POST",
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                        json=payload,
                    ) as response:
                        raise_provider_error(response)
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                event = json.loads(line[5:].strip())
                            except json.JSONDecodeError as exc:
                                raise ServerError(
                                    "Invalid streaming response from provider"
                                ) from exc
                            delta = event.get("delta", {})
                            if event.get("type") == "content_block_delta" and isinstance(
                                delta, dict
                            ):
                                text = delta.get("text")
                                if isinstance(text, str):
                                    sent_text = True
                                    yield text
                    return
                except AuthError as exc:
                    if sent_text:
                        raise
                    last_auth_error = exc
            if last_auth_error is not None:
                raise last_auth_error
        except httpx.TimeoutException as exc:
            raise Timeout("Provider stream timed out") from exc
        except httpx.RequestError as exc:
            raise ServerError("Provider stream connection failed") from exc

    async def health(self) -> bool:
        """Report whether credentials are configured without making a provider call."""
        return bool(self._api_keys)

    @staticmethod
    def _configured_keys(primary: str | None, fallbacks: str) -> list[str]:
        """Return the primary provider key followed by configured rotation keys."""
        return [key.strip() for key in [primary or "", *fallbacks.split(",")] if key.strip()]
