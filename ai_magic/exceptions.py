class AIMagicError(Exception):
    """Base library error."""


class ConfigurationError(AIMagicError):
    pass


class ProviderError(AIMagicError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RateLimitError(ProviderError):
    pass


class AuthenticationError(ProviderError):
    pass


class AllKeysUnavailableError(AIMagicError):
    pass
