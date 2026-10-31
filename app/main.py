"""FastAPI application factory and process entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.admin import router as admin_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.models import router as models_router
from app.api.usage import router as usage_router
from app.cache.response import ResponseCache
from app.config import Settings, get_settings
from app.db.session import create_session_factory
from app.hardening.concurrency import ProviderConcurrencyLimiter
from app.hardening.policies import HardeningPolicy
from app.hardening.request_size import RequestSizeLimitMiddleware
from app.limits.budget import BudgetManager
from app.limits.policies import RateLimitPolicy
from app.limits.pricing import PricingCatalog
from app.limits.token_bucket import TokenBucketLimiter
from app.limits.usage_writer import UsageWriter
from app.observability.logging import configure_json_logging
from app.observability.middleware import ObservabilityMiddleware, RequestTracker
from app.observability.policies import ObservabilityPolicy
from app.observability.tracing import configure_tracing
from app.providers.registry import create_providers
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.policies import FallbackPolicy
from app.routing.router import ProviderRouter
from app.routing.routes import RouteCatalog


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the gateway app with managed shared HTTP client lifecycle."""
    resolved_settings = settings or get_settings()
    hardening_policy = HardeningPolicy.load(resolved_settings.hardening_config_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_json_logging(resolved_settings.log_level)
        observability_policy = ObservabilityPolicy.load(resolved_settings.observability_config_path)
        application.state.observability_policy = observability_policy
        configure_tracing(
            resolved_settings.app_name,
            resolved_settings.otel_exporter_otlp_endpoint,
            observability_policy.trace_sample_ratio,
        )
        application.state.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(resolved_settings.request_timeout_seconds)
        )
        application.state.settings = resolved_settings
        application.state.providers = create_providers(
            resolved_settings, application.state.http_client
        )
        application.state.routes = RouteCatalog(resolved_settings.routes_config_path)
        application.state.redis = Redis.from_url(resolved_settings.redis_url, decode_responses=True)
        application.state.response_cache = ResponseCache(
            application.state.redis, hardening_policy.response_cache_ttl_seconds
        )
        fallback_policy = FallbackPolicy.load(resolved_settings.fallback_config_path)
        if hardening_policy.provider_concurrency_lease_seconds <= (
            fallback_policy.overall_timeout_seconds
        ):
            raise ValueError("Provider concurrency lease must exceed the overall request timeout")
        application.state.fallback_policy = fallback_policy
        application.state.hardening_policy = hardening_policy
        application.state.circuit_breaker = CircuitBreaker(application.state.redis, fallback_policy)
        application.state.routing_engine = ProviderRouter(
            application.state.providers,
            application.state.circuit_breaker,
            fallback_policy,
            ProviderConcurrencyLimiter(application.state.redis, hardening_policy),
        )
        rate_policy = RateLimitPolicy.load(resolved_settings.rate_limits_config_path)
        application.state.rate_policy = rate_policy
        application.state.rate_limiter = TokenBucketLimiter(application.state.redis, rate_policy)
        application.state.pricing = PricingCatalog.load(
            resolved_settings.pricing_config_path, rate_policy.characters_per_token_estimate
        )
        application.state.budget_manager = BudgetManager(application.state.redis)
        if resolved_settings.database_url:
            engine, session_factory = create_session_factory(resolved_settings.database_url)
            application.state.db_engine = engine
            application.state.session_factory = session_factory
            application.state.usage_writer = UsageWriter(
                session_factory, rate_policy.usage_queue_size
            )
            await application.state.usage_writer.start()
        else:
            application.state.db_engine = None
            application.state.session_factory = None
            application.state.usage_writer = None
        try:
            yield
        finally:
            drained = await application.state.request_tracker.drain(
                hardening_policy.graceful_shutdown_timeout_seconds
            )
            if not drained:
                import logging

                logging.getLogger(__name__).warning("shutdown_request_drain_timeout")
            if application.state.usage_writer is not None:
                await application.state.usage_writer.close()
            await application.state.http_client.aclose()
            await application.state.redis.aclose()
            if application.state.db_engine is not None:
                await application.state.db_engine.dispose()
            application.state.http_client = None

    application = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    application.state.request_tracker = RequestTracker()
    application.add_middleware(
        RequestSizeLimitMiddleware, max_bytes=hardening_policy.max_request_body_bytes
    )
    application.add_middleware(ObservabilityMiddleware)
    application.include_router(health_router)
    application.include_router(chat_router)
    application.include_router(admin_router)
    application.include_router(models_router)
    application.include_router(usage_router)
    application.include_router(metrics_router)
    return application


app = create_app()
