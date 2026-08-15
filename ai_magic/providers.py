from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .adapters import CohereAdapter, GeminiAdapter, OpenAIAdapter, ProviderAdapter
from .dto import ChatCompletion, ChatCompletionRequest
from .exceptions import AllKeysUnavailableError, ProviderError
from .state import KeyManager
from .transport import AsyncTransport


class Provider(Protocol):
    async def create(self, request: ChatCompletionRequest) -> ChatCompletion: ...


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    endpoint: str = "/chat/completions"
    headers: Mapping[str, str] = field(default_factory=dict)
    adapter: ProviderAdapter = field(default_factory=OpenAIAdapter)
    auth: str = "bearer"

    def url(self, model: str) -> str:
        return f"{self.base_url.rstrip('/')}/{self.endpoint.lstrip('/').format(model=model)}"


class ProviderRegistry:
    def __init__(self, configs: Mapping[str, ProviderConfig] | None = None) -> None:
        self._configs: dict[str, ProviderConfig] = {}
        for config in (configs or builtin_provider_configs()).values():
            self.register(config)

    def register(self, config: ProviderConfig) -> ProviderConfig:
        self._configs[config.name.lower()] = config
        return config

    def register_openai_compatible(self, name: str, base_url: str, *, endpoint: str = "/chat/completions", headers: Mapping[str, str] | None = None) -> ProviderConfig:
        return self.register(ProviderConfig(name=name, base_url=base_url, endpoint=endpoint, headers=dict(headers or {})))

    def get(self, name: str) -> ProviderConfig:
        try:
            return self._configs[name.lower()]
        except KeyError as exc:
            raise ValueError(f"Unknown provider: {name}") from exc

    def __contains__(self, name: str) -> bool:
        return name.lower() in self._configs


def builtin_provider_configs(*, openrouter_referer: str | None = None, openrouter_title: str | None = None) -> dict[str, ProviderConfig]:
    openrouter_headers = {}
    if openrouter_referer:
        openrouter_headers["HTTP-Referer"] = openrouter_referer
    if openrouter_title:
        openrouter_headers["X-Title"] = openrouter_title
    configs = [
        ProviderConfig("groq", "https://api.groq.com/openai/v1"),
        ProviderConfig("nvidia", "https://integrate.api.nvidia.com/v1"),
        ProviderConfig("openrouter", "https://openrouter.ai/api/v1", headers=openrouter_headers),
        ProviderConfig("gemini", "https://generativelanguage.googleapis.com/v1beta", endpoint="/models/{model}:generateContent", adapter=GeminiAdapter(), auth="query"),
        ProviderConfig("together", "https://api.together.xyz/v1"),
        ProviderConfig("mistral", "https://api.mistral.ai/v1"),
        ProviderConfig("cohere", "https://api.cohere.ai/v1", endpoint="/chat", adapter=CohereAdapter()),
        ProviderConfig("hyperbolic", "https://api.hyperbolic.xyz/v1"),
    ]
    return {config.name: config for config in configs}


class CarouselProvider:
    def __init__(self, transport: AsyncTransport, credentials: KeyManager, registry: ProviderRegistry) -> None:
        self.transport = transport
        self.credentials = credentials
        self.registry = registry

    @staticmethod
    def _retryable(exc: BaseException) -> bool:
        return isinstance(exc, httpx.TransportError) or isinstance(exc, ProviderError) and (exc.status_code == 429 or (exc.status_code or 0) >= 500)

    async def create(self, request: ChatCompletionRequest) -> ChatCompletion:
        """Try each compatible credential once.

        An explicit OpenAI-style ``request.model`` is authoritative and filters
        credentials by their model allow-list. Without it, each credential uses
        its own default model, preserving cross-provider rotation.
        """
        last_error: BaseException | None = None
        attempted: set[int] = set()
        explicit_model = request.model
        predicate = (lambda item: item.supports_model(explicit_model)) if explicit_model else None
        for _ in range(len(self.credentials)):
            credential = await self.credentials.acquire_credential(predicate=predicate)
            if id(credential) in attempted:
                break
            attempted.add(id(credential))
            config = self.registry.get(credential.provider)
            model = explicit_model or credential.default_model()
            if not model:
                raise ValueError(f"No model configured for provider {credential.provider}")
            headers = {**config.headers, **credential.headers}
            params = None
            if config.auth == "query":
                params = {"key": credential.key}
            else:
                headers["Authorization"] = f"Bearer {credential.key}"
            try:
                data = await self.transport.post(config.url(model), headers=headers, params=params, json=config.adapter.build(request, model))
                return config.adapter.parse(data, model)
            except Exception as exc:
                if not self._retryable(exc):
                    raise
                last_error = exc
                await self.credentials.ban(credential, getattr(exc, "retry_after", None) or 1.0)
        if last_error:
            raise last_error
        raise AllKeysUnavailableError("No compatible credentials are available")


class GroqProvider(CarouselProvider):
    def __init__(self, transport: AsyncTransport, keys: KeyManager, base_url: str) -> None:
        registry = ProviderRegistry({"groq": ProviderConfig("groq", base_url)})
        super().__init__(transport, keys, registry)


class GeminiProvider(CarouselProvider):
    def __init__(self, transport: AsyncTransport, keys: KeyManager, base_url: str) -> None:
        registry = ProviderRegistry({"gemini": ProviderConfig("gemini", base_url, endpoint="/models/{model}:generateContent", adapter=GeminiAdapter(), auth="query")})
        super().__init__(transport, keys, registry)
