from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from ._logging import logger
from .exceptions import AuthenticationError, ProviderError, RateLimitError


def parse_retry_after(value: str | None, default: float = 1.0) -> float:
    """Parse a Retry-After delta or HTTP date into non-negative seconds.

    Args:
        value: Header value as seconds or an HTTP date.
        default: Value returned for missing or invalid input.

    Returns:
        Remaining delay in seconds, clamped to zero.
    """
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return max(0.0, (dt - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return default


def _retryable(exc: BaseException) -> bool:
    # 429 is handled by the carousel so Retry-After can ban and rotate the key.
    return isinstance(exc, httpx.TransportError) or (
        isinstance(exc, ProviderError)
        and not isinstance(exc, (AuthenticationError, RateLimitError))
        and (exc.status_code == 408 or (exc.status_code or 0) >= 500)
    )


class AsyncTransport:
    """Asynchronous JSON transport with bounded retries and typed HTTP errors."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, timeout: float = 60, max_retries: int = 0) -> None:
        """Initialize the transport.

        Args:
            client: Optional caller-owned HTTP client. If omitted, a client is
                created and later closed by ``aclose()``.
            timeout: Timeout used only for an internally created client.
            max_retries: Additional attempts for transport errors, HTTP 408, and
                HTTP 5xx responses. HTTP 429 is left to credential rotation.
        """
        self._owned = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.max_retries = max_retries

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json: dict[str, Any],
    ) -> dict[str, Any]:
        """POST a JSON document and return the decoded JSON object.

        Args:
            url: Absolute request URL.
            headers: Optional request headers.
            params: Optional string query parameters.
            json: JSON object sent as the request body.

        Returns:
            Decoded JSON response object.

        Raises:
            RateLimitError: On HTTP 429; this method does not retry it.
            AuthenticationError: On HTTP 401 or 403.
            ProviderError: On other HTTP errors, after configured retries.
            httpx.TransportError: If transport retries are exhausted.
            ValueError: If a successful response is not valid JSON.
        """
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_random_exponential(multiplier=0.5, max=8),
            retry=retry_if_exception(_retryable),
            reraise=True,
        ):
            with attempt:
                response = await self.client.post(url, headers=headers, params=params, json=json)
                if response.status_code >= 400:
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    message = response.text
                    if response.status_code == 429:
                        logger.debug("HTTP 429 from %s, retry_after=%.1f", url, retry_after)
                        raise RateLimitError(message, status_code=429, retry_after=retry_after)
                    if response.status_code in {401, 403}:
                        logger.warning("HTTP %d authentication error from %s", response.status_code, url)
                        raise AuthenticationError(message, status_code=response.status_code)
                    logger.debug("HTTP %d from %s, retry_after=%.1f", response.status_code, url, retry_after)
                    raise ProviderError(message, status_code=response.status_code, retry_after=retry_after)
                return response.json()
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        """Close the HTTP client only when this transport created it."""
        if self._owned:
            await self.client.aclose()
