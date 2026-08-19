class AIMagicError(Exception):
    """Base class for errors raised intentionally by ai_magic."""


class ConfigurationError(AIMagicError):
    """Indicate invalid library configuration outside model validation."""


class ProviderError(AIMagicError):
    """Represent an unsuccessful or malformed provider response.

    Args:
        message: Human-readable provider error detail.
        status_code: HTTP status code when a response was received.
        retry_after: Suggested delay in seconds before retrying, when available.
    """

    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimitError(ProviderError):
    """Indicate an HTTP 429 provider response with optional retry timing."""


class AuthenticationError(ProviderError):
    """Indicate that a provider rejected request credentials or authorization."""


class AllKeysUnavailableError(AIMagicError):
    """Indicate that no compatible, currently usable credential is available."""
