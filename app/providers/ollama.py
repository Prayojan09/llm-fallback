"""Ollama local chat API adapter."""

import json
import time
import uuid
from collections.abc import AsyncIterator

import httpx

from app.config import Settings
from app.providers.errors import ServerError, Timeout
from app.providers.http import post_json, raise_provider_error
from app.schemas.chat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Usage,
)


class OllamaProvider:
    """Translate gateway chat requests to Ollama's /api/chat endpoint."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        """Store the shared transport and local Ollama endpoint."""
        self._client = client
        self._base_url = settings.ollama_base_url.rstrip("/")

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Generate a response with Ollama and normalize completion usage."""
        options: dict[str, object] = {}
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.stop is not None:
            options["stop"] = [request.stop] if isinstance(request.stop, str) else request.stop
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": False,
        }
        if options:
            payload["options"] = options
        result = await post_json(
            self._client, f"{self._base_url}/api/chat", headers={}, payload=payload
        )
        message = result.get("message", {})
        message = message if isinstance(message, dict) else {}
        input_tokens = int(result.get("prompt_eval_count", 0))
        output_tokens = int(result.get("eval_count", 0))
        reason = result.get("done_reason")
        finish_reason = "length" if reason == "length" else "stop"
        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=str(result.get("model", request.model or "unknown")),
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=str(message.get("content", ""))),
                    finish_reason=finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield text deltas from Ollama's newline-delimited JSON stream."""
        options: dict[str, object] = {}
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.stop is not None:
            options["stop"] = [request.stop] if isinstance(request.stop, str) else request.stop
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [message.model_dump() for message in request.messages],
            "stream": True,
        }
        if options:
            payload["options"] = options
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/api/chat", headers={}, json=payload
            ) as response:
                raise_provider_error(response)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ServerError("Invalid streaming response from provider") from exc
                    message = event.get("message", {})
                    if isinstance(message, dict) and isinstance(message.get("content"), str):
                        yield message["content"]
        except httpx.TimeoutException as exc:
            raise Timeout("Provider stream timed out") from exc
        except httpx.RequestError as exc:
            raise ServerError("Provider stream connection failed") from exc

    async def health(self) -> bool:
        """Check Ollama's non-generative tags endpoint."""
        try:
            response = await self._client.get(f"{self._base_url}/api/tags")
        except httpx.RequestError:
            return False
        return response.is_success
