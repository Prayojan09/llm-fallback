"""Provider-neutral errors used by routing and API layers."""


class ProviderError(Exception):
    """Base class for normalized provider failures."""


class RateLimited(ProviderError):
    """The provider rejected the request due to rate limits."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Retain an optional upstream Retry-After duration."""
        super().__init__(message)
        self.retry_after = retry_after


class Timeout(ProviderError):
    """The provider did not respond before its timeout."""


ProviderTimeout = Timeout


class ServerError(ProviderError):
    """The provider returned a server-side failure."""


class AuthError(ProviderError):
    """The provider rejected its configured credentials."""


class BadRequest(ProviderError):
    """The provider rejected the request as invalid."""
