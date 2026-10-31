"""Provider translation checks using mocked HTTP responses."""

import json

import httpx

from app.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider
from app.providers.openrouter import OpenRouterProvider
from app.routing.routes import RouteCatalog
from app.schemas.chat import ChatCompletionRequest


def test_anthropic_request_and_response_translation() -> None:
    """Anthropic system, stop, token, usage, and finish fields are translated."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-test",
                "content": [{"type": "text", "text": "Hello"}],
                "stop_reason": "max_tokens",
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        client,
        Settings(provider="fake", anthropic_api_key="test-secret", anthropic_default_max_tokens=80),
    )
    request = ChatCompletionRequest(
        model="claude-test",
        messages=[
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hi"},
        ],
        stop=["END"],
    )

    import asyncio

    response = asyncio.run(provider.chat(request))
    asyncio.run(client.aclose())

    assert captured["system"] == "Be helpful"
    assert captured["max_tokens"] == 80
    assert captured["stop_sequences"] == ["END"]
    assert response.choices[0].message.content == "Hello"
    assert response.choices[0].finish_reason == "length"
    assert response.usage.prompt_tokens == 4
    assert response.usage.completion_tokens == 2


def test_ollama_request_translation() -> None:
    """Ollama receives native chat fields and its token usage is normalized."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "llama3",
                "message": {"role": "assistant", "content": "Hello"},
                "done_reason": "stop",
                "prompt_eval_count": 5,
                "eval_count": 3,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider(client, Settings(provider="fake", ollama_base_url="http://ollama"))

    import asyncio

    response = asyncio.run(
        provider.chat(
            ChatCompletionRequest(
                model="llama3",
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=40,
                temperature=0.2,
                stop="END",
            )
        )
    )
    asyncio.run(client.aclose())

    assert captured["stream"] is False
    assert captured["options"] == {"num_predict": 40, "temperature": 0.2, "stop": ["END"]}
    assert response.usage.total_tokens == 8
    assert response.choices[0].finish_reason == "stop"


def test_openai_provider_reuses_openai_response_shape() -> None:
    """OpenAI-compatible upstream JSON is normalized without exposing secrets."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_test",
                "created": 1,
                "model": "gpt-test",
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(client, Settings(provider="fake", openai_api_key="test-secret"))

    import asyncio

    response = asyncio.run(
        provider.chat(
            ChatCompletionRequest(model="gpt-test", messages=[{"role": "user", "content": "Hi"}])
        )
    )
    asyncio.run(client.aclose())

    assert response.model == "gpt-test"
    assert response.usage.total_tokens == 3
    assert "test-secret" not in response.model_dump_json()


def test_openrouter_provider_uses_configured_endpoint_and_key() -> None:
    """OpenRouter requests use its configured API endpoint and secret key."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_test",
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        client,
        Settings(
            openrouter_api_key="test-openrouter-secret",
            openrouter_base_url="https://router.test/api/v1/",
        ),
    )

    import asyncio

    response = asyncio.run(
        provider.chat(
            ChatCompletionRequest(
                model="anthropic/claude-haiku-5.5",
                messages=[{"role": "user", "content": "Hi"}],
            )
        )
    )
    asyncio.run(client.aclose())

    assert captured == {
        "url": "https://router.test/api/v1/chat/completions",
        "authorization": "Bearer test-openrouter-secret",
    }
    assert response.choices[0].message.content == "Hello"


def test_route_catalog_lists_aliases_and_resolves_primary() -> None:
    """Aliases resolve to their first configured target before failover work."""
    catalog = RouteCatalog("config/routes.yaml")

    target = catalog.resolve("smart", "fake-model")

    assert target.provider == "openai"
    assert target.model == "gpt-4o"
    assert {"smart", "fast", "claude-3-5-sonnet-latest"}.issubset(catalog.list_models())
