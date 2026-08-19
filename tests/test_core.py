import asyncio
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from ai_magic import chat
from ai_magic.adapters import CohereAdapter
from ai_magic.client import AsyncAIMagic
from ai_magic.config import Settings
from ai_magic.dto import ChatCompletion, ChatCompletionRequest, ChatMessage, Choice
from ai_magic.exceptions import RateLimitError
from ai_magic.providers import CarouselProvider, ProviderRegistry, builtin_provider_configs
from ai_magic.state import Credential, KeyManager, SessionManager
from ai_magic.transport import parse_retry_after


@pytest.mark.asyncio
async def test_key_manager_round_robin_and_ban():
    manager = KeyManager(["a", "b"])
    assert await manager.acquire() == "a"
    await manager.ban("b", 60)
    assert await manager.acquire() == "a"


def test_retry_after_seconds_and_date():
    assert parse_retry_after("3") == 3
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=5))
    assert 0 <= parse_retry_after(future) <= 6


@pytest.mark.asyncio
async def test_session_preserves_system_prompt():
    sessions = SessionManager(2)
    result = await sessions.prepare(
        "s", [ChatMessage(role="system", content="rules"), ChatMessage(role="user", content="one")]
    )
    await sessions.append("s", ChatMessage(role="assistant", content="two"))
    result = await sessions.prepare("s", [ChatMessage(role="user", content="three")])
    assert any(m.role == "system" and m.content == "rules" for m in result)


class FakeProvider:
    def __init__(self):
        self.models = []

    async def create(self, request):
        self.models.append(request.model)
        if len(self.models) == 1:
            raise RateLimitError("limited", status_code=429, retry_after=1)
        return ChatCompletion(
            model=request.model, choices=[Choice(message=ChatMessage(role="assistant", content="ok"))]
        )


@pytest.mark.asyncio
async def test_empty_session_id_is_preserved():
    provider = FakeProvider()
    settings = Settings(groq_api_keys=["x"])
    sessions = SessionManager()
    client = AsyncAIMagic(settings, provider=provider, sessions=sessions)

    await client.chat.completions.create(
        messages=[{"role": "user", "content": "hi"}],
        session_id="",
    )

    assert "" in sessions._sessions
    assert sessions._sessions[""][-1].content == "ok"
    await client.aclose()


@pytest.mark.asyncio
async def test_model_fallback_without_network():
    provider = FakeProvider()
    settings = Settings(groq_api_keys=["x"])
    client = AsyncAIMagic(settings, provider=provider)
    response = await client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert response.model == settings.fallback_model
    assert provider.models == [None, settings.fallback_model]
    await client.aclose()


def test_exact_gemini_python_config_and_model_id_validation():
    settings = Settings(
        credentials=[
            {
                "provider": "gemini",
                "key": "placeholder-not-a-real-key",
                "models": ["gemini-3.7-flash", "gemini-3.6-flash"],
            }
        ],
        primary_model="gemini-3.7-flash",
        fallback_model="gemini-3.6-flash",
        timeout=60,
        max_retries=0,
        max_credential_wait=30,
    )
    client = AsyncAIMagic(settings)
    assert client.settings is settings
    asyncio.run(client.aclose())

    with pytest.raises(ValueError, match="Gemini model IDs"):
        Settings(
            credentials=[
                {
                    "provider": "gemini",
                    "key": "placeholder",
                    "models": ["Gemini-3.7-Flash"],
                }
            ]
        )


def test_registry_and_openrouter_headers():
    registry = ProviderRegistry(
        builtin_provider_configs(openrouter_referer="https://app.example", openrouter_title="AI Magic")
    )
    assert {"groq", "nvidia", "openrouter", "gemini", "together", "mistral", "cohere", "hyperbolic"} <= {
        name for name in registry._configs
    }
    assert registry.get("openrouter").headers == {"HTTP-Referer": "https://app.example", "X-Title": "AI Magic"}
    registry.register_openai_compatible("custom", "https://custom.example/v1")
    assert registry.get("custom").url("model") == "https://custom.example/v1/chat/completions"


def test_cohere_adapter_mapping_and_response():
    adapter = CohereAdapter()
    request = ChatCompletionRequest(
        model="command-r",
        messages=[
            ChatMessage(role="system", content="Be concise"),
            ChatMessage(role="user", content="first"),
            ChatMessage(role="assistant", content="answer"),
            ChatMessage(role="user", content="next"),
        ],
    )
    payload = adapter.build(request, "command-r")
    assert payload["preamble"] == "Be concise"
    assert payload["message"] == "next"
    assert payload["chat_history"] == [{"role": "USER", "message": "first"}, {"role": "CHATBOT", "message": "answer"}]
    response = adapter.parse(
        {
            "generation_id": "g",
            "text": "done",
            "finish_reason": "COMPLETE",
            "meta": {"billed_units": {"input_tokens": 2, "output_tokens": 3}},
        },
        "command-r",
    )
    assert response.choices[0].message.content == "done"
    assert response.usage.total_tokens == 5


class CarouselTransport:
    def __init__(self):
        self.calls = []

    async def aclose(self):
        pass

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if len(self.calls) == 1:
            raise RateLimitError("limited", status_code=429, retry_after=30)
        return {"model": "groq-model", "choices": [{"message": {"role": "assistant", "content": "ok"}}]}


@pytest.mark.asyncio
async def test_cross_provider_carousel_on_429():
    transport = CarouselTransport()
    manager = KeyManager(
        [
            Credential(provider="cohere", key="secret-one", model="command-r"),
            Credential(provider="groq", key="secret-two", model="groq-model"),
        ]
    )
    provider = CarouselProvider(transport, manager, ProviderRegistry())
    response = await provider.create(ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")]))
    assert response.choices[0].message.content == "ok"
    assert transport.calls[0][0].endswith("/v1/chat")
    assert transport.calls[1][0].endswith("/openai/v1/chat/completions")


@pytest.mark.asyncio
async def test_high_level_chat_with_injected_client_rotates_per_credential_defaults():
    transport = CarouselTransport()
    settings = Settings(
        credentials=[
            {"provider": "cohere", "key": "one", "models": ["command-r"]},
            {"provider": "groq", "key": "two", "models": ["groq-model"]},
        ],
        fallback_model="groq-model",
    )
    client = AsyncAIMagic(settings, transport=transport)
    result = await chat("hi", client=client)
    assert result == "ok"
    assert transport.calls[0][0].endswith("/v1/chat")
    assert transport.calls[1][0].endswith("/openai/v1/chat/completions")
    await client.aclose()


class RecordingTransport:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if len(self.calls) <= self.failures:
            raise RateLimitError("limited", status_code=429, retry_after=0.01)
        model = kwargs["json"].get("model", "native")
        return {"model": model, "choices": [{"message": {"role": "assistant", "content": "ok"}}]}


@pytest.mark.asyncio
async def test_single_provider_key_rotation():
    transport = RecordingTransport(failures=1)
    manager = KeyManager(
        [
            Credential("groq", "one", models=("primary",)),
            Credential("groq", "two", models=("primary",)),
        ]
    )
    response = await CarouselProvider(transport, manager, ProviderRegistry()).create(
        ChatCompletionRequest(model="primary", messages=[ChatMessage(role="user", content="hi")])
    )
    assert response.model == "primary"
    assert [call[1]["headers"]["Authorization"] for call in transport.calls] == ["Bearer one", "Bearer two"]


@pytest.mark.asyncio
async def test_explicit_fallback_model_overrides_credential_default():
    transport = RecordingTransport()
    manager = KeyManager([Credential("groq", "key", model="primary", models=("primary", "fallback"))])
    response = await CarouselProvider(transport, manager, ProviderRegistry()).create(
        ChatCompletionRequest(model="fallback", messages=[ChatMessage(role="user", content="hi")])
    )
    assert response.model == "fallback"
    assert transport.calls[0][1]["json"]["model"] == "fallback"


@pytest.mark.asyncio
async def test_provider_aware_model_selection_skips_incompatible_provider():
    transport = RecordingTransport()
    manager = KeyManager(
        [
            Credential("gemini", "gemini-key", models=("gemini-2.0-flash",)),
            Credential("cohere", "cohere-key", models=("command-r",)),
            Credential("groq", "groq-key", models=("llama-fallback",)),
        ]
    )
    response = await CarouselProvider(transport, manager, ProviderRegistry()).create(
        ChatCompletionRequest(model="llama-fallback", messages=[ChatMessage(role="user", content="hi")])
    )
    assert response.model == "llama-fallback"
    assert len(transport.calls) == 1
    assert "api.groq.com" in transport.calls[0][0]


@pytest.mark.asyncio
async def test_waits_for_short_unban_without_holding_lock():
    credential = Credential("groq", "key", model="model")
    manager = KeyManager([credential], max_wait=0.2)
    await manager.ban(credential, 0.03)
    started = time.monotonic()
    acquired = await manager.acquire_credential()
    assert acquired is credential
    assert time.monotonic() - started >= 0.02


@pytest.mark.asyncio
async def test_retry_after_429_is_not_retried_inside_transport():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "7"}, text="limited")

    from ai_magic.transport import AsyncTransport

    transport = AsyncTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=3)
    with pytest.raises(RateLimitError) as caught:
        await transport.post("https://example.test/chat", json={})
    assert calls == 1
    assert caught.value.retry_after == 7
    await transport.aclose()


@pytest.mark.asyncio
async def test_max_wait_prevents_infinite_unban_loop():
    credential = Credential("groq", "key", model="model")
    manager = KeyManager([credential], max_wait=0.01)
    await manager.ban(credential, 1)
    from ai_magic.exceptions import AllKeysUnavailableError

    with pytest.raises(AllKeysUnavailableError):
        await asyncio.wait_for(manager.acquire_credential(), timeout=0.1)
