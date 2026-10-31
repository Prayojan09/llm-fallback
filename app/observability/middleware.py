"""ASGI request correlation and chat request metrics middleware."""

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry.trace import SpanKind

from app.observability.logging import request_id_var
from app.observability.metrics import REQUEST_DURATION, REQUESTS
from app.observability.tracing import get_tracer
from app.schemas.errors import OpenAIError, OpenAIErrorResponse

ASGIMessage = dict[str, Any]
ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[ASGIMessage]], Callable[..., Awaitable[None]]],
    Awaitable[None],
]
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestTracker:
    """Track in-flight ASGI requests and stop admission during shutdown."""

    def __init__(self) -> None:
        """Create an empty request counter and condition."""
        self._condition = asyncio.Condition()
        self._active = 0
        self._draining = False

    async def enter(self) -> bool:
        """Register a request unless shutdown has started."""
        async with self._condition:
            if self._draining:
                return False
            self._active += 1
            return True

    async def leave(self) -> None:
        """Complete one in-flight request and wake shutdown waiters."""
        async with self._condition:
            self._active -= 1
            self._condition.notify_all()

    async def drain(self, timeout_seconds: float) -> bool:
        """Stop new request admission and wait for current requests to finish."""
        async with self._condition:
            self._draining = True
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._active == 0), timeout_seconds
                )
            except TimeoutError:
                return False
            return True


class ObservabilityMiddleware:
    """Propagate request IDs and capture one low-cardinality chat metric series."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the gateway ASGI application."""
        self._app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[ASGIMessage]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        """Attach safe correlation headers, spans, and end-to-end metrics."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        supplied_id = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = supplied_id if _SAFE_REQUEST_ID.fullmatch(supplied_id) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        tracker: RequestTracker | None = (
            getattr(scope.get("app").state, "request_tracker", None)
            if scope.get("app") is not None
            else None
        )
        if tracker is not None and not await tracker.enter():
            body = (
                OpenAIErrorResponse(
                    error=OpenAIError(
                        message="Gateway is shutting down",
                        type="server_error",
                        code="shutting_down",
                    )
                )
                .model_dump_json()
                .encode("utf-8")
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"x-request-id", request_id.encode("ascii")),
                        (b"retry-after", b"1"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            request_id_var.reset(token)
            return
        state = scope.setdefault("state", {})
        telemetry = state.setdefault("telemetry", {})
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: ASGIMessage) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                message["headers"].append((b"x-request-id", request_id.encode("ascii")))
            await send(message)

        span_name = (
            "chat.completion" if scope.get("path") == "/v1/chat/completions" else "http.request"
        )
        try:
            with get_tracer().start_as_current_span(span_name, kind=SpanKind.SERVER) as span:
                span.set_attribute("http.request.method", scope.get("method", ""))
                span.set_attribute("http.route", scope.get("path", ""))
                span.set_attribute("gateway.request_id", request_id)
                await self._app(scope, receive, send_with_request_id)
        finally:
            if scope.get("path") == "/v1/chat/completions":
                team = str(telemetry.get("team", "unknown"))
                model = str(telemetry.get("model", "unknown"))
                provider = str(telemetry.get("provider", "unknown"))
                outcome = str(telemetry.get("status", f"{status_code // 100}xx"))
                REQUESTS.labels(team, model, provider, outcome).inc()
                REQUEST_DURATION.labels(team, model).observe(time.perf_counter() - started)
            request_id_var.reset(token)
            if tracker is not None:
                await tracker.leave()
