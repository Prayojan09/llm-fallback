"""OpenRouter adapter using its OpenAI-compatible chat completions API."""

import httpx

from app.config import Settings
from app.providers.openai import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """Send normalized requests to OpenRouter with OpenRouter credentials."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        """Configure the OpenAI-compatible adapter with OpenRouter settings."""
        super().__init__(
            client,
            settings,
            base_url=settings.openrouter_base_url,
            api_keys=self._configured_keys(
                settings.openrouter_api_key, settings.openrouter_api_key_fallbacks
            ),
            provider_name="OpenRouter",
        )
