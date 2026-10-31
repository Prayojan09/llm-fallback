"""Provider chain execution with retries, deadlines, and circuit breakers."""

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.hardening.concurrency import ProviderConcurrencyLimiter
from app.observability.metrics import FALLBACKS, PROVIDER_ATTEMPTS
from app.observability.tracing import get_tracer
from app.providers.base import Provider
from app.providers.errors import ProviderError, RateLimited, ServerError, Timeout
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.policies import FallbackPolicy
from app.routing.routes import ProviderTarget
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse


@dataclass(frozen=True)
class RoutedResponse:
    """Completion plus the provider metadata selected by the router."""

    response: ChatCompletionResponse
    provider: str
    model: str
    attempts: int
    fallback_used: bool


class RoutingError(Exception):
    """Base error raised when a provider chain cannot complete a request."""


class NoProvidersAvailable(RoutingError):
    """Every provider was skipped because its breaker is open."""


class AllProvidersFailed(RoutingError):
    """At least one provider was attempted but no target succeeded."""


class OverallRequestTimeout(RoutingError):
    """The full retry and fallback sequence exceeded its deadline."""


class RoutingUnavailable(RoutingError):
    """Required shared routing state could not be read from Redis."""


class ProviderRouter:
    """Run configured chains without depending on provider-specific exceptions."""

    def __init__(
        self,
        providers: dict[str, Provider],
        breaker: CircuitBreaker,
        policy: FallbackPolicy,
        concurrency: ProviderConcurrencyLimiter | None = None,
    ) -> None:
        """Store swappable provider implementations and routing policy."""
        self._providers = providers
        self._breaker = breaker
        self._policy = policy
        self._concurrency = concurrency

    async def chat(
        self, request: ChatCompletionRequest, chain: list[ProviderTarget]
    ) -> RoutedResponse:
        """Try each target with configured retries under one overall deadline."""
        try:
            async with asyncio.timeout(self._policy.overall_timeout_seconds):
                return await self._run_chain(request, chain)
        except TimeoutError as exc:
            raise OverallRequestTimeout("Overall provider routing deadline exceeded") from exc
        except RedisError as exc:
            raise RoutingUnavailable("Circuit breaker store is unavailable") from exc

    async def stream(
        self, request: ChatCompletionRequest, chain: list[ProviderTarget]
    ) -> AsyncIterator[tuple[str, str, str, bool, int]]:
        """Yield provider, model, text, and fallback metadata for a routed stream."""
        try:
            async with asyncio.timeout(self._policy.overall_timeout_seconds):
                attempted = 0
                last_error: ProviderError | None = None
                for target_index, target in enumerate(chain):
                    provider = self._providers.get(target.provider)
                    if provider is None:
                        continue
                    for retry_index in range(self._policy.retries_per_provider + 1):
                        if retry_index:
                            await self._backoff(retry_index, last_error)
                        lease_id = (
                            await self._concurrency.acquire(target.provider)
                            if self._concurrency is not None
                            else None
                        )
                        if self._concurrency is not None and lease_id is None:
                            break
                        try:
                            allowed = await self._breaker.allow(target.provider)
                        except Exception:
                            if self._concurrency is not None and lease_id is not None:
                                await self._concurrency.release(target.provider, lease_id)
                            raise
                        if not allowed:
                            if self._concurrency is not None and lease_id is not None:
                                await self._concurrency.release(target.provider, lease_id)
                            break
                        attempted += 1
                        source = provider.stream(request.model_copy(update={"model": target.model}))
                        sent_text = False
                        fallback_recorded = False
                        try:
                            with get_tracer().start_as_current_span(
                                "provider.attempt",
                                attributes={
                                    "provider": target.provider,
                                    "model": target.model,
                                    "attempt": attempted,
                                },
                            ) as span:
                                try:
                                    async with asyncio.timeout(
                                        self._policy.attempt_timeout_seconds
                                    ):
                                        async for text in source:
                                            if target_index > 0 and not fallback_recorded:
                                                FALLBACKS.labels(
                                                    chain[target_index - 1].provider,
                                                    target.provider,
                                                ).inc()
                                                fallback_recorded = True
                                            sent_text = True
                                            yield (
                                                target.provider,
                                                target.model,
                                                text,
                                                target_index > 0,
                                                attempted,
                                            )
                                except TimeoutError:
                                    span.set_attribute("outcome", "timeout")
                                    raise
                                except ProviderError:
                                    span.set_attribute("outcome", "failure")
                                    raise
                                span.set_attribute("outcome", "success")
                                PROVIDER_ATTEMPTS.labels(
                                    target.provider, target.model, "success"
                                ).inc()
                        except TimeoutError as exc:
                            error: ProviderError = Timeout("Provider stream timed out")
                            error.__cause__ = exc
                            PROVIDER_ATTEMPTS.labels(target.provider, target.model, "timeout").inc()
                        except ProviderError as exc:
                            error = exc
                            PROVIDER_ATTEMPTS.labels(target.provider, target.model, "failure").inc()
                        else:
                            await self._breaker.record_success(target.provider)
                            if target_index > 0 and not fallback_recorded:
                                FALLBACKS.labels(
                                    chain[target_index - 1].provider, target.provider
                                ).inc()
                            if not sent_text:
                                yield target.provider, target.model, "", target_index > 0, attempted
                            return
                        finally:
                            if self._concurrency is not None and lease_id is not None:
                                await self._concurrency.release(target.provider, lease_id)
                        if not self._is_retryable(error):
                            raise error
                        last_error = error
                        await self._breaker.record_failure(target.provider)
                        if sent_text:
                            raise error
                if attempted == 0:
                    raise NoProvidersAvailable("No provider is currently available")
                raise AllProvidersFailed("All configured providers failed") from last_error
        except TimeoutError as exc:
            raise OverallRequestTimeout("Overall provider routing deadline exceeded") from exc
        except RedisError as exc:
            raise RoutingUnavailable("Circuit breaker store is unavailable") from exc

    async def _run_chain(
        self, request: ChatCompletionRequest, chain: list[ProviderTarget]
    ) -> RoutedResponse:
        """Execute targets and same-target retries in configured chain order."""
        attempted = 0
        last_error: ProviderError | None = None
        for target_index, target in enumerate(chain):
            provider = self._providers.get(target.provider)
            if provider is None:
                continue
            for retry_index in range(self._policy.retries_per_provider + 1):
                if retry_index:
                    await self._backoff(retry_index, last_error)
                lease_id = (
                    await self._concurrency.acquire(target.provider)
                    if self._concurrency is not None
                    else None
                )
                if self._concurrency is not None and lease_id is None:
                    break
                try:
                    allowed = await self._breaker.allow(target.provider)
                except Exception:
                    if self._concurrency is not None and lease_id is not None:
                        await self._concurrency.release(target.provider, lease_id)
                    raise
                if not allowed:
                    if self._concurrency is not None and lease_id is not None:
                        await self._concurrency.release(target.provider, lease_id)
                    break
                attempted += 1
                provider_request = request.model_copy(update={"model": target.model})
                try:
                    with get_tracer().start_as_current_span(
                        "provider.attempt",
                        attributes={
                            "provider": target.provider,
                            "model": target.model,
                            "attempt": attempted,
                        },
                    ) as span:
                        try:
                            async with asyncio.timeout(self._policy.attempt_timeout_seconds):
                                result = await provider.chat(provider_request)
                        except TimeoutError:
                            span.set_attribute("outcome", "timeout")
                            raise
                        except ProviderError:
                            span.set_attribute("outcome", "failure")
                            raise
                        span.set_attribute("outcome", "success")
                        PROVIDER_ATTEMPTS.labels(target.provider, target.model, "success").inc()
                except TimeoutError as exc:
                    error = Timeout("Provider attempt timed out")
                    error.__cause__ = exc
                    PROVIDER_ATTEMPTS.labels(target.provider, target.model, "timeout").inc()
                except ProviderError as exc:
                    error = exc
                    PROVIDER_ATTEMPTS.labels(target.provider, target.model, "failure").inc()
                else:
                    await self._breaker.record_success(target.provider)
                    if target_index > 0:
                        FALLBACKS.labels(chain[target_index - 1].provider, target.provider).inc()
                    return RoutedResponse(
                        response=result,
                        provider=target.provider,
                        model=target.model,
                        attempts=attempted,
                        fallback_used=target_index > 0,
                    )
                finally:
                    if self._concurrency is not None and lease_id is not None:
                        await self._concurrency.release(target.provider, lease_id)

                if not self._is_retryable(error):
                    raise error
                last_error = error
                await self._breaker.record_failure(target.provider)
            # Continue to the next configured provider once retries are exhausted.
        if attempted == 0:
            raise NoProvidersAvailable("No provider is currently available")
        raise AllProvidersFailed("All configured providers failed") from last_error

    async def _backoff(self, retry_index: int, error: ProviderError | None) -> None:
        """Sleep for capped exponential backoff plus jitter, honoring Retry-After."""
        delay = min(
            self._policy.retry_max_seconds,
            self._policy.retry_base_seconds * (2 ** (retry_index - 1)),
        )
        delay += random.uniform(0, self._policy.retry_jitter_seconds)
        retry_after = error.retry_after if isinstance(error, RateLimited) else None
        if retry_after is not None:
            delay = max(delay, retry_after)
        await asyncio.sleep(delay)

    @staticmethod
    def _is_retryable(error: ProviderError) -> bool:
        """Classify only timeouts, rate limits, and server/connection errors as transient."""
        return isinstance(error, RateLimited | ServerError | Timeout)
