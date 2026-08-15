from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential

from .exceptions import AuthenticationError, ProviderError, RateLimitError


def parse_retry_after(value: str | None, default: float = 1.0) -> float:
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
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
    def __init__(self, client: httpx.AsyncClient | None = None, *, timeout: float = 60, max_retries: int = 0) -> None:
        self._owned = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self.max_retries = max_retries

    async def post(self, url: str, *, headers: dict[str, str] | None = None, params: dict[str, str] | None = None, json: dict[str, Any]) -> dict[str, Any]:
        async for attempt in AsyncRetrying(stop=stop_after_attempt(self.max_retries + 1), wait=wait_random_exponential(multiplier=0.5, max=8), retry=retry_if_exception(_retryable), reraise=True):
            with attempt:
                response = await self.client.post(url, headers=headers, params=params, json=json)
                if response.status_code >= 400:
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    message = response.text
                    if response.status_code == 429:
                        raise RateLimitError(message, status_code=429, retry_after=retry_after)
                    if response.status_code in {401, 403}:
                        raise AuthenticationError(message, status_code=response.status_code)
                    raise ProviderError(message, status_code=response.status_code, retry_after=retry_after)
                return response.json()
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        if self._owned:
            await self.client.aclose()
