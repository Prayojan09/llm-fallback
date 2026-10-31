"""Provider construction and lookup."""

import httpx

from app.config import Settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import Provider
from app.providers.fake import FakeProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider
from app.providers.openrouter import OpenRouterProvider


def create_providers(settings: Settings, client: httpx.AsyncClient) -> dict[str, Provider]:
    """Create all provider adapters with the same pooled HTTP client."""
    return {
        "fake": FakeProvider(),
        "fake_backup": FakeProvider(),
        "openai": OpenAIProvider(client, settings),
        "openrouter": OpenRouterProvider(client, settings),
        "anthropic": AnthropicProvider(client, settings),
        "ollama": OllamaProvider(client, settings),
    }
