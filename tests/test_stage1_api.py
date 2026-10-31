"""Stage 1 API checks using the built-in fake provider."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.team import authenticate_team
from app.config import Settings
from app.limits.budget_dependency import get_budget_manager
from app.limits.dependencies import get_rate_limiter
from app.limits.token_bucket import RateLimitResult
from app.main import create_app
from app.schemas.teams import TeamAuth


def create_test_app(cache_enabled: bool = False) -> FastAPI:
    """Create an app with an injected demo team for earlier gateway API checks."""

    class AlwaysAllowLimiter:
        """A deterministic limiter for API tests unrelated to Redis."""

        def estimate_tokens(self, request) -> int:
            """Return a minimal token estimate."""
            return 1

        async def consume(self, team: TeamAuth, token_cost: int) -> RateLimitResult:
            """Allow the request and supply complete response metadata."""
            return RateLimitResult(True, 59, 999, 1, 1)

    application = create_app(Settings(provider="fake"))
    application.dependency_overrides[authenticate_team] = lambda: TeamAuth(
        id="test-team",
        name="Test Team",
        monthly_budget_usd=100,
        rpm_limit=60,
        tpm_limit=100_000,
        allowed_models=["*"],
        active=True,
        cache_enabled=cache_enabled,
    )
    application.dependency_overrides[get_rate_limiter] = lambda: AlwaysAllowLimiter()

    class UnlimitedBudget:
        """Budget stub for earlier API tests with no Redis dependency."""

        @staticmethod
        def month_key() -> str:
            """Provide a stable budget period to the endpoint."""
            return "test-month"

        async def reserve(
            self,
            team_id: str,
            monthly_budget_usd: float,
            estimate_usd: float,
            period: str,
        ) -> float:
            """Return no warning for the unlimited demo budget."""
            return 0.0

        async def reconcile(
            self,
            team_id: str,
            monthly_budget_usd: float,
            reserved_usd: float,
            actual_usd: float,
            period: str,
        ) -> float:
            """Reconcile the fake provider's usage without external storage."""
            return 0.0

        async def release(
            self,
            team_id: str,
            monthly_budget_usd: float,
            reserved_usd: float,
            period: str,
        ) -> None:
            """Release a reservation after a simulated failure."""

    application.dependency_overrides[get_budget_manager] = lambda: UnlimitedBudget()
    return application


def test_health_and_readiness() -> None:
    """Health endpoints distinguish liveness from initialized readiness."""
    with TestClient(create_test_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}


def test_chat_completion_uses_fake_provider() -> None:
    """The chat endpoint returns an OpenAI-shaped response without external calls."""
    with TestClient(create_test_app()) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "fake-model", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "fake-model"
    assert body["choices"][0]["message"]["content"] == "Fake response: Hi"
    assert body["usage"]["total_tokens"] > 0


def test_streaming_returns_openai_compatible_server_sent_events() -> None:
    """Streaming returns typed OpenAI-compatible chunks and the terminal event."""
    with TestClient(create_test_app()) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}], "stream": True},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"object":"chat.completion.chunk"' in response.text
    assert '"content":"Fake ' in response.text
    assert "data: [DONE]" in response.text


def test_oversized_request_body_is_rejected_before_parsing() -> None:
    """The configured request body ceiling returns a safe JSON 413 response."""
    with TestClient(create_test_app()) as client:
        response = client.post("/v1/chat/completions", content=b"x" * 1_048_577)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
