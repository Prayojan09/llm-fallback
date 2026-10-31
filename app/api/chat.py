"""OpenAI-compatible chat completion endpoint."""

import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth.team import authenticate_team
from app.cache.response import CachedCompletion
from app.limits.budget import BudgetExceeded, BudgetManager
from app.limits.budget_dependency import get_budget_manager
from app.limits.dependencies import get_rate_limiter
from app.limits.token_bucket import (
    RateLimitExceeded,
    RateLimitResult,
    RateLimitUnavailable,
    TokenBucketLimiter,
)
from app.limits.usage_writer import UsageRecord
from app.observability.metrics import BUDGET_USED_RATIO, RATE_LIMITED, record_usage
from app.observability.tracing import get_tracer
from app.providers.errors import ProviderError
from app.routing.router import (
    AllProvidersFailed,
    NoProvidersAvailable,
    OverallRequestTimeout,
    RoutingUnavailable,
)
from app.routing.routes import ProviderTarget
from app.schemas.chat import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from app.schemas.errors import OpenAIError, OpenAIErrorResponse
from app.schemas.teams import TeamAuth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    body: ChatCompletionRequest,
    request: Request,
    response: Response,
    team: TeamAuth = Depends(authenticate_team),
    limiter: TokenBucketLimiter = Depends(get_rate_limiter),
    budget_manager: BudgetManager = Depends(get_budget_manager),
) -> ChatCompletionResponse | JSONResponse | StreamingResponse:
    """Authenticate, enforce limits, reserve budget, call provider, and record usage."""
    requested_model = body.model or request.app.state.settings.default_model
    telemetry = request.scope.setdefault("state", {}).setdefault("telemetry", {})
    telemetry["model"] = requested_model
    started = time.perf_counter()
    reserved_usd = 0.0
    reserved = False
    budget_period = budget_manager.month_key()
    target: ProviderTarget | None = None
    cache_key: str | None = None
    chain: list[ProviderTarget] = []
    try:
        telemetry["team"] = team.id
        chain = request.app.state.routes.chain(body.model, request.app.state.settings.default_model)
        if "*" not in team.allowed_models:
            if requested_model in team.allowed_models:
                pass
            else:
                chain = [target for target in chain if target.model in team.allowed_models]
        if not chain:
            raise HTTPException(status_code=403, detail="Model is not allowed for this team")
        target = chain[0]

        pricing = request.app.state.pricing
        input_estimate = pricing.estimate_input_tokens(body, target.model)
        output_estimate = body.max_tokens or request.app.state.rate_policy.default_completion_tokens
        estimated_total_tokens = input_estimate + output_estimate
        with get_tracer().start_as_current_span("rate_limit"):
            limit = await limiter.consume(team, estimated_total_tokens)
        if not limit.allowed:
            RATE_LIMITED.labels(team.id).inc()
            error = OpenAIErrorResponse(
                error=OpenAIError(
                    message="Rate limit exceeded. Please retry after the indicated delay.",
                    type="rate_limit_error",
                    code="rate_limit_exceeded",
                )
            )
            headers = limit.response_headers(team)
            headers["Retry-After"] = str(max(1, limit.retry_after or 1))
            return JSONResponse(status_code=429, content=error.model_dump(), headers=headers)
        response.headers.update(limit.response_headers(team))

        if team.cache_enabled and not body.stream and body.temperature == 0:
            cache = request.app.state.response_cache
            cache_key = cache.key(team.id, body, chain)
            cached = await cache.get(cache_key)
            if cached is not None:
                response.headers["X-Gateway-Cache"] = "HIT"
                response.headers["X-Gateway-Provider"] = cached.provider
                response.headers["X-Gateway-Attempts"] = "0"
                response.headers["X-Gateway-Fallback"] = str(cached.fallback_used).lower()
                telemetry["provider"] = cached.provider
                usage = cached.response.usage
                record_usage(team.id, usage.prompt_tokens, usage.completion_tokens, 0.0)
                _enqueue_usage(
                    request,
                    UsageRecord(
                        team_id=team.id,
                        api_key_id=team.api_key_id,
                        requested_model=requested_model,
                        served_provider=cached.provider,
                        served_model=cached.model,
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens,
                        cost_usd=0.0,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        fallback_used=cached.fallback_used,
                        status="cache_hit",
                    ),
                )
                return cached.response
            response.headers["X-Gateway-Cache"] = "MISS"

        reserved_usd = max(
            pricing.estimate_cost(candidate.model, input_estimate, output_estimate)
            for candidate in chain
        )
        with get_tracer().start_as_current_span("budget.reserve"):
            budget_ratio = await budget_manager.reserve(
                team.id, team.monthly_budget_usd, reserved_usd, budget_period
            )
        BUDGET_USED_RATIO.labels(team.id).set(budget_ratio)
        reserved = True
        if (
            team.monthly_budget_usd > 0
            and budget_ratio >= request.app.state.rate_policy.budget_warn_ratio
        ):
            response.headers["X-Gateway-Budget-Warning"] = "approaching_limit"

        routed_request = body.model_copy(update={"max_tokens": output_estimate})
        if body.stream:
            stream_iterator = request.app.state.routing_engine.stream(routed_request, chain)
            first_chunk = await anext(stream_iterator)
            stream_id = f"chatcmpl-{uuid.uuid4().hex}"
            stream_created = int(time.time())
            response.headers["X-Gateway-Provider"] = first_chunk[0]
            telemetry["provider"] = first_chunk[0]
            response.headers["X-Gateway-Fallback"] = str(first_chunk[3]).lower()
            response.headers["X-Gateway-Attempts"] = str(first_chunk[4])
            response.headers["X-Gateway-RateLimit-Status"] = response.headers.get(
                "X-Gateway-RateLimit-Status", "ok"
            )

            async def event_stream() -> AsyncIterator[str]:
                """Serialize routed provider deltas and reconcile stream usage."""
                chunks = [first_chunk[2]] if first_chunk[2] else []
                selected_provider, selected_model, _, fallback_used, _ = first_chunk
                complete = False
                try:
                    yield _sse_chunk(
                        stream_id,
                        stream_created,
                        selected_model,
                        ChatCompletionChunkDelta(role="assistant"),
                    )
                    if first_chunk[2]:
                        yield _sse_chunk(
                            stream_id,
                            stream_created,
                            selected_model,
                            ChatCompletionChunkDelta(content=first_chunk[2]),
                        )
                    async for (
                        provider_name,
                        model_name,
                        text,
                        was_fallback,
                        _attempt_count,
                    ) in stream_iterator:
                        selected_provider, selected_model = provider_name, model_name
                        fallback_used = was_fallback
                        if text:
                            chunks.append(text)
                            yield _sse_chunk(
                                stream_id,
                                stream_created,
                                model_name,
                                ChatCompletionChunkDelta(content=text),
                            )
                    yield _sse_chunk(
                        stream_id,
                        stream_created,
                        selected_model,
                        ChatCompletionChunkDelta(),
                        finish_reason="stop",
                    )
                    complete = True
                except Exception:
                    telemetry["status"] = "error"
                    logger.exception("provider_stream_interrupted")
                    error_event = (
                        "data: "
                        + OpenAIErrorResponse(
                            error=OpenAIError(
                                message="Provider stream interrupted", type="server_error"
                            )
                        ).model_dump_json(exclude_none=True)
                        + "\n\n"
                    )
                    yield error_event
                finally:
                    text = "".join(chunks)
                    input_tokens = pricing.estimate_input_tokens(body, selected_model)
                    output_tokens = (
                        len(text) + request.app.state.rate_policy.characters_per_token_estimate - 1
                    ) // request.app.state.rate_policy.characters_per_token_estimate
                    actual_cost = pricing.actual_cost(selected_model, input_tokens, output_tokens)
                    with get_tracer().start_as_current_span("budget.reconcile"):
                        actual_budget_ratio = await budget_manager.reconcile(
                            team.id,
                            team.monthly_budget_usd,
                            reserved_usd,
                            actual_cost,
                            budget_period,
                        )
                    BUDGET_USED_RATIO.labels(team.id).set(actual_budget_ratio)
                    record_usage(team.id, input_tokens, output_tokens, actual_cost)
                    _enqueue_usage(
                        request,
                        UsageRecord(
                            team_id=team.id,
                            api_key_id=team.api_key_id,
                            requested_model=requested_model,
                            served_provider=selected_provider,
                            served_model=selected_model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=actual_cost,
                            latency_ms=int((time.perf_counter() - started) * 1000),
                            fallback_used=fallback_used,
                            status="success" if complete else "partial",
                        ),
                    )
                    yield "data: [DONE]\n\n"

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    **dict(response.headers),
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        routed = await request.app.state.routing_engine.chat(routed_request, chain)
        target = ProviderTarget(provider=routed.provider, model=routed.model)
        telemetry["provider"] = routed.provider
        response.headers["X-Gateway-Provider"] = routed.provider
        response.headers["X-Gateway-Attempts"] = str(routed.attempts)
        response.headers["X-Gateway-Fallback"] = str(routed.fallback_used).lower()
        usage = routed.response.usage
        actual_cost = pricing.actual_cost(
            target.model, usage.prompt_tokens, usage.completion_tokens
        )
        with get_tracer().start_as_current_span("budget.reconcile"):
            updated_budget_ratio = await budget_manager.reconcile(
                team.id, team.monthly_budget_usd, reserved_usd, actual_cost, budget_period
            )
        BUDGET_USED_RATIO.labels(team.id).set(updated_budget_ratio)
        record_usage(team.id, usage.prompt_tokens, usage.completion_tokens, actual_cost)
        reserved = False
        if (
            team.monthly_budget_usd > 0
            and updated_budget_ratio >= request.app.state.rate_policy.budget_warn_ratio
        ):
            response.headers["X-Gateway-Budget-Warning"] = "approaching_limit"
        _enqueue_usage(
            request,
            UsageRecord(
                team_id=team.id,
                api_key_id=team.api_key_id,
                requested_model=requested_model,
                served_provider=target.provider,
                served_model=target.model,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cost_usd=actual_cost,
                latency_ms=int((time.perf_counter() - started) * 1000),
                fallback_used=routed.fallback_used,
                status="success",
            ),
        )
        if cache_key is not None:
            await request.app.state.response_cache.set(
                cache_key,
                CachedCompletion(
                    response=routed.response,
                    provider=routed.provider,
                    model=routed.model,
                    fallback_used=routed.fallback_used,
                ),
            )
        return routed.response
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown model: {exc.args[0]}") from exc
    except BudgetExceeded:
        return JSONResponse(
            status_code=402,
            content=OpenAIErrorResponse(
                error=OpenAIError(
                    message="Estimated request cost exceeds the team's monthly budget.",
                    type="budget_exceeded",
                    code="monthly_budget_exceeded",
                )
            ).model_dump(),
        )
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="Rate or budget service is unavailable"
        ) from exc
    except RateLimitExceeded as exc:
        RATE_LIMITED.labels(team.id).inc()
        headers = RateLimitResult(
            allowed=False,
            request_remaining=0,
            token_remaining=0,
            request_reset=exc.retry_after,
            token_reset=exc.retry_after,
        ).response_headers(team)
        headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=429,
            content=OpenAIErrorResponse(
                error=OpenAIError(
                    message="Requested token estimate exceeds the team bucket capacity.",
                    type="rate_limit_error",
                    code="rate_limit_exceeded",
                )
            ).model_dump(),
            headers=headers,
        )
    except OverallRequestTimeout as exc:
        _enqueue_failed_usage(request, team, requested_model, target, started)
        await _release_budget(request, budget_manager, team, reserved_usd, reserved, budget_period)
        raise HTTPException(status_code=504, detail="Provider routing deadline exceeded") from exc
    except (NoProvidersAvailable, AllProvidersFailed, RoutingUnavailable) as exc:
        _enqueue_failed_usage(request, team, requested_model, target, started)
        await _release_budget(request, budget_manager, team, reserved_usd, reserved, budget_period)
        status_code = 503 if isinstance(exc, NoProvidersAvailable | RoutingUnavailable) else 502
        raise HTTPException(
            status_code=status_code, detail="No provider could serve the request"
        ) from exc
    except ProviderError as exc:
        if reserved:
            try:
                await budget_manager.release(
                    team.id, team.monthly_budget_usd, reserved_usd, budget_period
                )
            except RateLimitUnavailable:
                logger.exception("budget_reservation_release_failed")
        _enqueue_failed_usage(request, team, requested_model, target, started)
        raise HTTPException(status_code=502, detail="Upstream provider request failed") from exc
    except Exception:
        await _release_budget(request, budget_manager, team, reserved_usd, reserved, budget_period)
        raise


def _enqueue_usage(request: Request, record: UsageRecord) -> None:
    """Send usage data to the non-blocking writer when database persistence is enabled."""
    writer = getattr(request.app.state, "usage_writer", None)
    if writer is not None:
        writer.enqueue(record)


def _sse_chunk(
    chunk_id: str,
    created: int,
    model: str,
    delta: ChatCompletionChunkDelta,
    finish_reason: str | None = None,
) -> str:
    """Serialize one typed OpenAI-compatible chat chunk as an SSE event."""
    chunk = ChatCompletionChunk(
        id=chunk_id,
        created=created,
        model=model,
        choices=[ChatCompletionChunkChoice(delta=delta, finish_reason=finish_reason)],
    )
    return f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


def _enqueue_failed_usage(
    request: Request,
    team: TeamAuth,
    requested_model: str,
    target: ProviderTarget | None,
    started: float,
) -> None:
    """Queue a failed request record without prompts or provider response data."""
    if target is None:
        return
    _enqueue_usage(
        request,
        UsageRecord(
            team_id=team.id,
            api_key_id=team.api_key_id,
            requested_model=requested_model,
            served_provider=target.provider,
            served_model=target.model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            fallback_used=False,
            status="error",
        ),
    )


async def _release_budget(
    request: Request,
    manager: BudgetManager,
    team: TeamAuth,
    reserved_usd: float,
    reserved: bool,
    period: str,
) -> None:
    """Release a pending reservation when routing fails."""
    if not reserved:
        return
    try:
        await manager.release(team.id, team.monthly_budget_usd, reserved_usd, period)
    except RateLimitUnavailable:
        logger.exception("budget_reservation_release_failed")
