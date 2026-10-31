"""OpenAI Chat Completions adapter."""

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


class OpenAIProvider:
    """Translate normalized gateway requests to OpenAI's chat API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_keys: list[str] | None = None,
        provider_name: str = "OpenAI",
    ) -> None:
        """Store shared transport and credentials without exposing the key."""
        self._client = client
        self._api_keys = api_keys if api_keys is not None else self._configured_keys(
            settings.openai_api_key, settings.openai_api_key_fallbacks
        )
        self._chat_completions_url = f"{base_url.rstrip('/')}/chat/completions"
        self._provider_name = provider_name

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Create and normalize an OpenAI chat completion."""
        if not self._api_keys:
            raise AuthError(f"{self._provider_name} API key is not configured")
        payload = request.model_dump(exclude_none=True, exclude={"stream"})
        result: dict[str, object] | None = None
        last_auth_error: AuthError | None = None
        for api_key in self._api_keys:
            try:
                result = await post_json(
                    self._client,
                    self._chat_completions_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    payload=payload,
                )
            except AuthError as exc:
                last_auth_error = exc
            else:
                break
        if result is None:
            raise last_auth_error or AuthError(f"{self._provider_name} credentials were rejected")
        choices_data = result.get("choices", [])
        first = choices_data[0] if isinstance(choices_data, list) and choices_data else {}
        message_data = first.get("message", {}) if isinstance(first, dict) else {}
        usage_data = result.get("usage", {})
        usage_data = usage_data if isinstance(usage_data, dict) else {}
        message_content = message_data.get("content", "") if isinstance(message_data, dict) else ""
        finish_reason = first.get("finish_reason", "stop") if isinstance(first, dict) else "stop"
        return ChatCompletionResponse(
            id=str(result.get("id", f"chatcmpl-{uuid.uuid4().hex}")),
            created=int(result.get("created", time.time())),
            model=str(result.get("model", request.model or "unknown")),
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=str(message_content)),
                    finish_reason=finish_reason
                    if finish_reason in {"stop", "length", "tool_calls"}
                    else "stop",
                )
            ],
            usage=Usage(
                prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
                completion_tokens=int(usage_data.get("completion_tokens", 0)),
                total_tokens=int(usage_data.get("total_tokens", 0)),
            ),
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield text deltas from OpenAI's server-sent event response."""
        if not self._api_keys:
            raise AuthError(f"{self._provider_name} API key is not configured")
        payload = request.model_dump(exclude_none=True, exclude={"stream"})
        last_auth_error: AuthError | None = None
        payload["stream"] = True
        try:
            for api_key in self._api_keys:
                sent_text = False
                try:
                    async with self._client.stream(
                        "POST",
                        self._chat_completions_url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    ) as response:
                        raise_provider_error(response)
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError as exc:
                                raise ServerError(
                                    "Invalid streaming response from provider"
                                ) from exc
                            choices = event.get("choices", [])
                            if choices and isinstance(choices[0], dict):
                                delta = choices[0].get("delta", {})
                                if isinstance(delta, dict) and isinstance(
                                    delta.get("content"), str
                                ):
                                    sent_text = True
                                    yield delta["content"]
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
        """Report whether credentials are configured without making a billable call."""
        return bool(self._api_keys)

    @staticmethod
    def _configured_keys(primary: str | None, fallbacks: str) -> list[str]:
        """Return the primary provider key followed by configured rotation keys."""
        return [key.strip() for key in [primary or "", *fallbacks.split(",")] if key.strip()]
