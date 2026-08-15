from __future__ import annotations

import pytest

from ai_magic import api
from ai_magic.dto import ChatCompletion, ChatMessage, Choice


class FakeCompletions:
    def __init__(self, content: str = "answer") -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> ChatCompletion:
        self.calls.append(kwargs)
        return ChatCompletion(
            model="fake",
            choices=[Choice(message=ChatMessage(role="assistant", content=self.content))],
        )


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeClient:
    def __init__(self, content: str = "answer") -> None:
        self.completions = FakeCompletions(content)
        self.chat = FakeChat(self.completions)
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_chat_builds_messages_normalizes_int_session_and_forwards_kwargs():
    client = FakeClient()

    result = await api.chat(
        "hello",
        client=client,
        session_id=42,
        system="rules",
        model="model-a",
        temperature=0.25,
        max_tokens=123,
    )

    assert result == "answer"
    assert client.completions.calls == [{
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
        ],
        "session_id": "42",
        "model": "model-a",
        "temperature": 0.25,
        "max_tokens": 123,
    }]
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_chat_reused_client_is_not_closed():
    client = FakeClient()

    await api.chat("one", client=client)
    await api.chat("two", client=client)

    assert len(client.completions.calls) == 2
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_chat_owned_client_is_closed(monkeypatch: pytest.MonkeyPatch):
    client = FakeClient()
    monkeypatch.setattr(api, "AsyncAIMagic", lambda: client)

    assert await api.chat("hello") == "answer"
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_chat_owned_client_is_closed_on_error(monkeypatch: pytest.MonkeyPatch):
    client = FakeClient()

    async def fail(**kwargs: object) -> ChatCompletion:
        raise RuntimeError("boom")

    client.completions.create = fail  # type: ignore[method-assign]
    monkeypatch.setattr(api, "AsyncAIMagic", lambda: client)

    with pytest.raises(RuntimeError, match="boom"):
        await api.chat("hello")
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_code_uses_strict_system_prompt_and_strips_outer_fence():
    client = FakeClient("```python\nprint('ok')\n```")

    result = await api.code("write it", client=client, session_id="session")

    assert result == "print('ok')"
    call = client.completions.calls[0]
    assert call["session_id"] == "session"
    system = call["messages"][0]["content"]  # type: ignore[index]
    assert "Return only the requested source code" in system
    assert "Do not include Markdown code fences" in system
    assert "explanations" in system


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("print('ok')", "print('ok')"),
        ("value = '```python\\nnot a fence\\n```'", "value = '```python\\nnot a fence\\n```'"),
        ("prefix\n```python\nprint('ok')\n```", "prefix\n```python\nprint('ok')\n```"),
        ("```python\nprint('ok')\n```\nsuffix", "```python\nprint('ok')\n```\nsuffix"),
        ("```python\nprint('```')\n```", "print('```')"),
    ],
)
def test_strip_outer_code_fence_is_conservative(value: str, expected: str):
    assert api._strip_outer_code_fence(value) == expected


@pytest.mark.asyncio
async def test_invalid_session_id_is_rejected_before_client_creation(monkeypatch: pytest.MonkeyPatch):
    def unexpected_client() -> FakeClient:
        raise AssertionError("client must not be created")

    monkeypatch.setattr(api, "AsyncAIMagic", unexpected_client)
    with pytest.raises(TypeError, match="session_id"):
        await api.chat("hello", session_id=True)
