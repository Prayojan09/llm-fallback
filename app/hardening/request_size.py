"""Reject oversized request bodies before FastAPI parses them."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.schemas.errors import OpenAIError, OpenAIErrorResponse

ASGIMessage = dict[str, Any]


class RequestBodyTooLarge(Exception):
    """Indicate that a streamed request exceeded the configured body limit."""


class RequestSizeLimitMiddleware:
    """Enforce a byte limit using both Content-Length and streamed body bytes."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        """Wrap an ASGI application with a validated maximum body size."""
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[ASGIMessage]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        """Reject bodies beyond the limit with a safe OpenAI-style 413 response."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        declared_length = headers.get(b"content-length", b"")
        if declared_length.isdigit() and int(declared_length) > self._max_bytes:
            await self._send_error(send)
            return
        received = 0

        async def limited_receive() -> ASGIMessage:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise RequestBodyTooLarge
            return message

        response_started = False

        async def tracking_send(message: ASGIMessage) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await self._send_error(send)

    async def _send_error(self, send: Callable[..., Awaitable[None]]) -> None:
        """Send a compact JSON 413 without reflecting submitted body content."""
        body = (
            OpenAIErrorResponse(
                error=OpenAIError(
                    message="Request body exceeds the configured size limit.",
                    type="invalid_request_error",
                    code="request_too_large",
                )
            )
            .model_dump_json()
            .encode("utf-8")
        )
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body})
