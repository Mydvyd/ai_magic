from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .dto import ChatMessage
from .exceptions import AllKeysUnavailableError


@dataclass(frozen=True, slots=True)
class Credential:
    provider: str
    key: str
    model: str | None = None
    models: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)

    def supports_model(self, model: str) -> bool:
        """Return whether an explicit model may be sent with this credential.

        ``models`` is the provider-aware allow-list. For backwards compatibility,
        a credential with only ``model`` supports that model. An unconfigured
        credential accepts an explicit OpenAI-style ``model=`` value.
        """
        configured = self.models or ((self.model,) if self.model else ())
        return not configured or model in configured

    def default_model(self) -> str | None:
        return self.model or (self.models[0] if self.models else None)

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("credential provider must not be empty")
        if not self.key:
            raise ValueError("credential key must not be empty")


CredentialInput = Credential | Mapping[str, Any] | str


class KeyManager:
    """Async-safe round-robin carousel of provider credentials.

    Plain strings remain supported for legacy callers and are returned as strings.
    New code should pass ``Credential`` objects (or equivalent mappings).
    """

    def __init__(
        self,
        credentials: Sequence[CredentialInput],
        *,
        default_provider: str = "groq",
        default_model: str | None = None,
        max_wait: float = 30.0,
    ) -> None:
        if not credentials:
            raise ValueError("credentials must not be empty")
        if max_wait < 0:
            raise ValueError("max_wait must be non-negative")
        self._legacy = all(isinstance(item, str) for item in credentials)
        self._credentials = tuple(self._coerce(item, default_provider, default_model) for item in credentials)
        self._banned_until: dict[int, float] = {}
        self._index = 0
        self.max_wait = max_wait
        self._lock = asyncio.Lock()

    @staticmethod
    def _coerce(item: CredentialInput, provider: str, model: str | None) -> Credential:
        if isinstance(item, Credential):
            return item
        if isinstance(item, str):
            return Credential(provider=provider, key=item, model=model)
        raw_models = item.get("models") or ()
        if isinstance(raw_models, str):
            raw_models = (raw_models,)
        return Credential(
            provider=str(item.get("provider", provider)),
            key=str(item["key"]),
            model=item.get("model", model),
            models=tuple(str(value) for value in raw_models),
            metadata=dict(item.get("metadata") or {}),
            headers=dict(item.get("headers") or {}),
        )

    async def acquire(self) -> Credential | str:
        credential = await self.acquire_credential()
        return credential.key if self._legacy else credential

    async def acquire_credential(
        self,
        *,
        predicate: Callable[[Credential], bool] | None = None,
        max_wait: float | None = None,
    ) -> Credential:
        """Acquire a compatible credential, waiting once for the nearest unban.

        The lock protects only selection state; it is always released before
        sleeping. ``max_wait`` is a per-call deadline and prevents endless loops.
        """
        deadline = time.monotonic() + (self.max_wait if max_wait is None else max(0.0, max_wait))
        while True:
            async with self._lock:
                now = time.monotonic()
                compatible = [item for item in self._credentials if predicate is None or predicate(item)]
                if not compatible:
                    raise AllKeysUnavailableError("No credential is compatible with the requested model")
                for _ in self._credentials:
                    credential = self._credentials[self._index]
                    self._index = (self._index + 1) % len(self._credentials)
                    if credential in compatible and self._banned_until.get(id(credential), 0) <= now:
                        return credential
                wait = min(self._banned_until.get(id(item), now) for item in compatible) - now
                remaining = deadline - now
            if wait <= 0:
                continue
            if wait > remaining:
                raise AllKeysUnavailableError(
                    f"All compatible credentials are temporarily unavailable; "
                    f"next unban in {wait:.2f}s exceeds max wait"
                )
            await asyncio.sleep(wait)

    async def ban(self, credential: Credential | str, seconds: float) -> None:
        async with self._lock:
            target = credential if isinstance(credential, Credential) else next((item for item in self._credentials if item.key == credential), None)
            if target is None:
                return
            identity = id(target)
            self._banned_until[identity] = max(self._banned_until.get(identity, 0), time.monotonic() + max(0, seconds))

    def __len__(self) -> int:
        return len(self._credentials)


Summarizer = Callable[[list[ChatMessage]], Awaitable[str]]


class SessionManager:
    def __init__(self, history_limit: int = 10) -> None:
        self.history_limit = history_limit
        self._sessions: dict[str, list[ChatMessage]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._summarizing: set[str] = set()

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def prepare(self, session_id: str, incoming: list[ChatMessage], summarizer: Summarizer | None = None) -> list[ChatMessage]:
        async with self._lock(session_id):
            history = self._sessions.setdefault(session_id, [])
            systems = [m for m in history + incoming if m.role == "system"]
            non_system = [m for m in history + incoming if m.role != "system"]
            if len(non_system) > self.history_limit and summarizer and session_id not in self._summarizing:
                self._summarizing.add(session_id)
                try:
                    old = non_system[:-self.history_limit + 1]
                    summary = await summarizer(old)
                    non_system = [ChatMessage(role="system", content=f"Conversation summary: {summary}")] + non_system[-self.history_limit + 1:]
                finally:
                    self._summarizing.discard(session_id)
            result = systems + non_system[-self.history_limit:]
            self._sessions[session_id] = result.copy()
            return result

    async def append(self, session_id: str, message: ChatMessage) -> None:
        async with self._lock(session_id):
            self._sessions.setdefault(session_id, []).append(message)
