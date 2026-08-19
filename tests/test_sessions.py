from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from ai_magic.client import AsyncAIMagic
from ai_magic.config import Settings
from ai_magic.dto import ChatMessage
from ai_magic.state import SessionManager


def message(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)


@pytest.mark.asyncio
async def test_session_manager_evicts_least_recently_used_session() -> None:
    sessions = SessionManager(max_sessions=2)
    await sessions.append("old", message("old"))
    await sessions.append("recent", message("recent"))
    await sessions.prepare("old", [])
    await sessions.append("new", message("new"))

    assert list(sessions._sessions) == ["old", "new"]
    assert "recent" not in sessions._locks
    assert "recent" not in sessions._summarizing
    assert await sessions.count() == 2


@pytest.mark.asyncio
async def test_session_manager_does_not_evict_active_session() -> None:
    sessions = SessionManager(history_limit=2, max_sessions=1)
    await sessions.append("active", message("first"))
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(_: list[ChatMessage]) -> str:
        started.set()
        await release.wait()
        return "summary"

    task = asyncio.create_task(sessions.prepare("active", [message("second"), message("third")], summarize))
    await started.wait()
    await sessions.append("other", message("other"))

    assert "active" in sessions._sessions
    assert await sessions.count() == 1
    release.set()
    await task
    assert not sessions._users
    assert not sessions._summarizing


@pytest.mark.asyncio
async def test_session_manager_serializes_concurrent_updates() -> None:
    sessions = SessionManager(history_limit=100, max_sessions=10)
    await asyncio.gather(*(sessions.append("shared", message(str(index))) for index in range(50)))

    assert len(sessions._sessions["shared"]) == 50
    assert await sessions.count() == 1


@pytest.mark.asyncio
async def test_session_manager_clear_specific_and_all_without_orphans() -> None:
    sessions = SessionManager(max_sessions=3)
    await sessions.append("one", message("one"))
    await sessions.append("two", message("two"))

    assert await sessions.clear("one") == 1
    assert "one" not in sessions._sessions
    assert "one" not in sessions._locks
    assert await sessions.clear("missing") == 0
    assert await sessions.clear() == 1
    assert await sessions.count() == 0
    assert not sessions._locks
    assert not sessions._summarizing
    assert not sessions._users


@pytest.mark.asyncio
async def test_clear_skips_active_session() -> None:
    sessions = SessionManager(history_limit=2, max_sessions=2)
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(_: list[ChatMessage]) -> str:
        started.set()
        await release.wait()
        return "summary"

    task = asyncio.create_task(sessions.prepare("active", [message("1"), message("2"), message("3")], summarize))
    await started.wait()
    assert await sessions.clear("active") == 0
    assert "active" in sessions._sessions
    release.set()
    await task
    assert await sessions.clear("active") == 1


def test_max_sessions_settings_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "placeholder")
    monkeypatch.setenv("AI_MAGIC_MAX_SESSIONS", "7")
    settings = Settings.from_env()
    client = AsyncAIMagic(settings)

    assert settings.max_sessions == 7
    assert client.sessions.max_sessions == 7
    asyncio.run(client.aclose())

    with pytest.raises(ValidationError):
        Settings(groq_api_keys=["placeholder"], max_sessions=0)


def test_invalid_max_sessions_environment_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "placeholder")
    monkeypatch.setenv("AI_MAGIC_MAX_SESSIONS", "invalid")
    assert Settings.from_env().max_sessions == 1000


def test_session_manager_constructor_remains_backward_compatible() -> None:
    sessions = SessionManager(2)
    assert sessions.history_limit == 2
    assert sessions.max_sessions == 1000
