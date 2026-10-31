"""Shared HTTP response handling for provider adapters."""

import httpx

from app.providers.errors import (
    AuthError,
    BadRequest,
    ProviderError,
    RateLimited,
    ServerError,
    Timeout,
)


def raise_provider_error(response: httpx.Response) -> None:
    """Raise the provider-neutral exception matching an unsuccessful response."""
    if response.is_success:
        return
    message = f"Provider returned HTTP {response.status_code}"
    if response.status_code == 400:
        raise BadRequest(message)
    if response.status_code in (401, 403):
        raise AuthError(message)
    if response.status_code == 429:
        retry_after: float | None = None
        raw_retry_after = response.headers.get("Retry-After")
        if raw_retry_after is not None:
            try:
                retry_after = max(0.0, float(raw_retry_after))
            except ValueError:
                retry_after = None
        raise RateLimited(message, retry_after=retry_after)
    if response.status_code >= 500:
        raise ServerError(message)
    raise ProviderError(message)


async def post_json(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str], payload: dict[str, object]
) -> dict[str, object]:
    """POST JSON and translate transport and status failures to normalized errors."""
    try:
        response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise Timeout("Provider request timed out") from exc
    except httpx.RequestError as exc:
        raise ServerError("Provider connection failed") from exc
    raise_provider_error(response)
    return response.json()
