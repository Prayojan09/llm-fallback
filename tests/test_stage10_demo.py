"""Stage 10 demo controls stay protected and affect only fake providers."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.admin import FakeProviderAvailability, set_fake_provider_availability
from app.providers.errors import ServerError
from app.providers.fake import FakeProvider
from app.schemas.chat import ChatCompletionRequest, ChatMessage


@pytest.mark.asyncio
async def test_demo_toggle_changes_fake_provider_availability() -> None:
    """The local admin control can disable and restore a FakeProvider instance."""
    provider = FakeProvider()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(environment="local"), providers={"fake": provider}
            )
        )
    )
    await set_fake_provider_availability("fake", FakeProviderAvailability(available=False), request)
    with pytest.raises(ServerError):
        await provider.chat(
            ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        )
    await set_fake_provider_availability("fake", FakeProviderAvailability(available=True), request)
    assert (
        await provider.chat(
            ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        )
    ).choices[0].message.content == "Fake response: hi"


@pytest.mark.asyncio
async def test_demo_toggle_is_hidden_in_production() -> None:
    """The fake-provider control is inaccessible in production mode."""
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(environment="production"), providers={})
        )
    )
    with pytest.raises(HTTPException) as error:
        await set_fake_provider_availability(
            "fake", FakeProviderAvailability(available=False), request
        )
    assert error.value.status_code == 404
