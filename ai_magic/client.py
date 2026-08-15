from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .dto import ChatCompletion, ChatCompletionRequest, ChatMessage
from .exceptions import AllKeysUnavailableError, ProviderError, RateLimitError
from .providers import CarouselProvider, Provider, ProviderRegistry, builtin_provider_configs
from .state import KeyManager, SessionManager
from .transport import AsyncTransport


class _Completions:
    def __init__(self, owner: "AsyncAIMagic") -> None:
        self.owner = owner

    async def create(self, *, messages: list[dict[str, Any] | ChatMessage], model: str | None = None, session_id: str | None = None, **kwargs: Any) -> ChatCompletion:
        parsed = [m if isinstance(m, ChatMessage) else ChatMessage.model_validate(m) for m in messages]
        if session_id is not None:
            parsed = await self.owner.sessions.prepare(session_id, parsed, self.owner._summarize)
        request = ChatCompletionRequest(messages=parsed, model=model, session_id=session_id, **kwargs)
        response = await self.owner._create_with_fallback(request)
        if session_id is not None:
            await self.owner.sessions.append(session_id, response.choices[0].message)
        return response


class _Chat:
    def __init__(self, owner: "AsyncAIMagic") -> None:
        self.completions = _Completions(owner)


class AsyncAIMagic:
    def __init__(self, settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None, transport: AsyncTransport | None = None, provider: Provider | None = None, registry: ProviderRegistry | None = None, credentials: KeyManager | None = None, sessions: SessionManager | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.transport = transport or AsyncTransport(http_client, timeout=self.settings.timeout, max_retries=self.settings.max_retries)
        configs = builtin_provider_configs(openrouter_referer=self.settings.openrouter_referer, openrouter_title=self.settings.openrouter_title)
        configs["groq"] = configs["groq"].__class__("groq", self.settings.groq_base_url)
        gemini = configs["gemini"]
        configs["gemini"] = gemini.__class__("gemini", self.settings.gemini_base_url, endpoint=gemini.endpoint, adapter=gemini.adapter, auth=gemini.auth)
        self.registry = registry or ProviderRegistry(configs)
        if credentials is None:
            items = self.settings.credentials
            if not items:
                keys = self.settings.groq_api_keys if self.settings.provider == "groq" else self.settings.gemini_api_keys
                items = [{"provider": self.settings.provider, "key": key, "model": self.settings.primary_model} for key in keys]
            credentials = KeyManager(items, max_wait=self.settings.max_credential_wait)
        self.credentials = credentials
        self.provider = provider or CarouselProvider(self.transport, self.credentials, self.registry)
        self.sessions = sessions or SessionManager(self.settings.default_history_limit)
        self.chat = _Chat(self)
        self._closed = False

    async def _create_with_fallback(self, request: ChatCompletionRequest) -> ChatCompletion:
        effective = request if request.model is not None else request.model_copy(update={"model": self.settings.primary_model})
        try:
            return await self.provider.create(effective)
        except (RateLimitError, AllKeysUnavailableError, ProviderError) as exc:
            retryable = not isinstance(exc, ProviderError) or exc.status_code == 429 or (exc.status_code or 0) >= 500
            if effective.model == self.settings.primary_model and self.settings.fallback_model != effective.model and retryable:
                return await self.provider.create(effective.model_copy(update={"model": self.settings.fallback_model}))
            raise

    async def _summarize(self, messages: list[ChatMessage]) -> str:
        prompt = [ChatMessage(role="system", content="Summarize the conversation faithfully and concisely. Preserve requirements, decisions, and unresolved questions."), ChatMessage(role="user", content="\n".join(f"{m.role}: {m.content}" for m in messages))]
        response = await self._create_with_fallback(ChatCompletionRequest(model=self.settings.fallback_model, messages=prompt))
        return response.choices[0].message.content

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self.transport.aclose()

    async def __aenter__(self) -> "AsyncAIMagic":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
