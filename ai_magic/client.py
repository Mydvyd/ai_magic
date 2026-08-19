from __future__ import annotations

import dataclasses
from typing import Any

import httpx

from ._logging import logger
from .config import Settings
from .dto import ChatCompletion, ChatCompletionRequest, ChatMessage
from .exceptions import AllKeysUnavailableError, ProviderError, RateLimitError
from .providers import CarouselProvider, Provider, ProviderRegistry, builtin_provider_configs
from .state import KeyManager, SessionManager
from .transport import AsyncTransport


def _ensure_choices(response: ChatCompletion) -> ChatCompletion:
    if not response.choices:
        raise ProviderError("Provider returned no completion choices", status_code=200)
    return response


class _Completions:
    def __init__(self, owner: AsyncAIMagic) -> None:
        self.owner = owner

    async def create(
        self,
        *,
        messages: list[dict[str, Any] | ChatMessage],
        model: str | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a chat completion through the configured provider carousel.

        Args:
            messages: Conversation messages as validated DTOs or dictionaries.
            model: Explicit model identifier. If omitted, each credential's
                configured default model is used during rotation.
            session_id: Optional conversation identifier used to retain history.
                Calls for the same session are serialized by ``SessionManager``.
            **kwargs: Additional fields accepted by ``ChatCompletionRequest``.

        Returns:
            A normalized chat completion containing at least one choice.

        Raises:
            ValueError: If streaming is requested, no model is available, or the
                provider returns no choices.
            pydantic.ValidationError: If messages or request fields are invalid.
            AIMagicError: If credentials are unavailable or a provider fails.
            httpx.TransportError: If all compatible transport attempts fail.
        """
        parsed = [m if isinstance(m, ChatMessage) else ChatMessage.model_validate(m) for m in messages]
        if session_id is not None:
            parsed = await self.owner.sessions.prepare(session_id, parsed, self.owner._summarize)
        if "stream" in kwargs and kwargs["stream"]:
            raise ValueError("Streaming is not supported")
        request = ChatCompletionRequest(messages=parsed, model=model, session_id=session_id, **kwargs)
        response = await self.owner._create_with_fallback(request)
        _ensure_choices(response)
        if session_id is not None:
            await self.owner.sessions.append(session_id, response.choices[0].message)
        return response


class _Chat:
    def __init__(self, owner: AsyncAIMagic) -> None:
        self.completions = _Completions(owner)


class AsyncAIMagic:
    """Asynchronous multi-provider chat client.

    The client owns only transports it creates itself. Reuse one instance for
    connection pooling and session history, and close it with ``aclose()`` or an
    asynchronous context manager. Concurrent requests are supported; credential
    selection and updates to the same session are serialized internally.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: AsyncTransport | None = None,
        provider: Provider | None = None,
        registry: ProviderRegistry | None = None,
        credentials: KeyManager | None = None,
        sessions: SessionManager | None = None,
    ) -> None:
        """Initialize the client and its provider, credential, and session state.

        Args:
            settings: Validated configuration. If omitted, configuration is read
                from environment variables by ``Settings.from_env()``.
            http_client: Optional HTTP client used by a newly created transport.
                It remains caller-owned and is not closed by this client.
            transport: Optional transport implementation. Takes precedence over
                ``http_client`` and is used for all provider requests.
            provider: Optional provider implementation, typically for a custom
                routing policy or tests.
            registry: Provider registry used when constructing the default
                carousel provider.
            credentials: Credential manager used by the default provider.
            sessions: Session manager used for conversation history.

        Raises:
            ValueError: If environment configuration or credentials are invalid.
            pydantic.ValidationError: If environment-backed settings are invalid.
        """
        self.settings = settings or Settings.from_env()
        self.transport = transport or AsyncTransport(
            http_client, timeout=self.settings.timeout, max_retries=self.settings.max_retries
        )
        configs = builtin_provider_configs(
            openrouter_referer=self.settings.openrouter_referer, openrouter_title=self.settings.openrouter_title
        )
        configs["groq"] = dataclasses.replace(configs["groq"], base_url=self.settings.groq_base_url)
        configs["gemini"] = dataclasses.replace(configs["gemini"], base_url=self.settings.gemini_base_url)
        self.registry = registry or ProviderRegistry(configs)
        if credentials is None:
            items = self.settings.credentials
            if not items:
                keys = (
                    self.settings.groq_api_keys if self.settings.provider == "groq" else self.settings.gemini_api_keys
                )
                items = [
                    {"provider": self.settings.provider, "key": key, "model": self.settings.primary_model}
                    for key in keys
                ]
            credentials = KeyManager(items, max_wait=self.settings.max_credential_wait)
        self.credentials = credentials
        self.provider = provider or CarouselProvider(self.transport, self.credentials, self.registry)
        self.sessions = sessions or SessionManager(
            self.settings.default_history_limit,
            max_sessions=self.settings.max_sessions,
        )
        self.chat = _Chat(self)
        self._closed = False

    async def _create_with_fallback(self, request: ChatCompletionRequest) -> ChatCompletion:
        """Create a completion while preserving explicit-vs-default model intent.

        ``model=None`` is deliberately forwarded unchanged: the carousel then uses
        each credential's own default model and can rotate across providers. A
        global fallback is always an explicit model, so the carousel's allow-list
        filtering prevents it from reaching an incompatible provider.
        """
        try:
            return await self.provider.create(request)
        except (RateLimitError, AllKeysUnavailableError, ProviderError) as exc:
            retryable = not isinstance(exc, ProviderError) or exc.status_code == 429 or (exc.status_code or 0) >= 500
            fallback = self.settings.fallback_model
            may_fallback = request.model is None or request.model == self.settings.primary_model
            if fallback and fallback != request.model and may_fallback and retryable:
                logger.info("Falling back to model=%s after %s", fallback, type(exc).__name__)
                return await self.provider.create(request.model_copy(update={"model": fallback}))
            raise

    async def _summarize(self, messages: list[ChatMessage]) -> str:
        logger.debug("Summarizing %d history messages for session compaction", len(messages))
        prompt = [
            ChatMessage(
                role="system",
                content=(
                    "Summarize the conversation faithfully and concisely. "
                    "Preserve requirements, decisions, and unresolved questions."
                ),
            ),
            ChatMessage(role="user", content="\n".join(f"{m.role}: {m.content}" for m in messages)),
        ]
        response = await self._create_with_fallback(ChatCompletionRequest(messages=prompt))
        _ensure_choices(response)
        return response.choices[0].message.content

    async def aclose(self) -> None:
        """Close the transport once.

        A transport backed by a caller-supplied ``httpx.AsyncClient`` leaves that
        HTTP client open; otherwise its internally created client is closed.
        Repeated calls are safe.
        """
        if not self._closed:
            self._closed = True
            await self.transport.aclose()

    async def __aenter__(self) -> AsyncAIMagic:
        """Return this client for use in an asynchronous context manager."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Close owned transport resources when leaving the context."""
        await self.aclose()
