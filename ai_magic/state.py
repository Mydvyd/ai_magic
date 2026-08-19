from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from ._logging import logger
from .dto import ChatMessage
from .exceptions import AllKeysUnavailableError


@dataclass(frozen=True, slots=True)
class Credential:
    """Provider credential with model routing metadata.

    Attributes:
        provider: Registry name of the provider that accepts the key.
        key: Secret API credential.
        model: Preferred model used when a request omits an explicit model.
        models: Allowed model identifiers; the first is the default when
            ``model`` is absent.
        metadata: Caller-defined metadata not sent automatically.
        headers: Credential-specific headers merged into provider headers.

    Raises:
        ValueError: If ``provider`` or ``key`` is empty.
    """

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
        """Return the preferred model, the first allowed model, or ``None``."""
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
        """Initialize credential rotation state.

        Args:
            credentials: Non-empty sequence of credentials, mappings, or legacy
                key strings.
            default_provider: Provider assigned to legacy strings and mappings
                without a provider.
            default_model: Model assigned when an input does not specify one.
            max_wait: Maximum seconds an acquisition waits for an unban.

        Raises:
            ValueError: If no credentials are supplied, ``max_wait`` is negative,
                or a credential mapping is invalid.
        """
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
        raw_key = item.get("key")
        if not raw_key:
            raise ValueError("Each credential mapping must contain a non-empty 'key' field")
        raw_models = item.get("models") or ()
        if isinstance(raw_models, str):
            raw_models = (raw_models,)
        return Credential(
            provider=str(item.get("provider", provider)),
            key=str(raw_key),
            model=item.get("model", model),
            models=tuple(str(value) for value in raw_models),
            metadata=dict(item.get("metadata") or {}),
            headers=dict(item.get("headers") or {}),
        )

    async def acquire(self) -> Credential | str:
        """Acquire the next available credential in round-robin order.

        Returns:
            A key string when initialized exclusively with legacy strings;
            otherwise the selected ``Credential``.

        Raises:
            AllKeysUnavailableError: If no credential becomes available within
                the configured wait limit.
        """
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
                logger.debug("Giving up on credential wait: next unban in %.2fs exceeds deadline", wait)
                raise AllKeysUnavailableError(
                    f"All compatible credentials are temporarily unavailable; "
                    f"next unban in {wait:.2f}s exceeds max wait"
                )
            logger.debug("All compatible credentials banned; sleeping %.2fs until next unban", wait)
            await asyncio.sleep(wait)

    async def ban(self, credential: Credential | str, seconds: float) -> None:
        """Temporarily exclude a credential from selection.

        Args:
            credential: Credential object or matching legacy key string.
            seconds: Ban duration; negative values are treated as zero. Repeated
                bans never shorten an existing ban.
        """
        async with self._lock:
            target = (
                credential
                if isinstance(credential, Credential)
                else next((item for item in self._credentials if item.key == credential), None)
            )
            if target is None:
                return
            identity = id(target)
            until = max(self._banned_until.get(identity, 0), time.monotonic() + max(0, seconds))
            self._banned_until[identity] = until
            logger.debug("Credential provider=%s banned for %.1fs", target.provider, max(0, seconds))

    def __len__(self) -> int:
        return len(self._credentials)


Summarizer = Callable[[list[ChatMessage]], Awaitable[str]]


class SessionManager:
    """Concurrency-safe in-memory conversation history with LRU eviction.

    Operations for the same session are serialized. Different sessions may be
    updated concurrently. Active or summarizing sessions are not cleared or
    evicted, so the retained count can temporarily exceed ``max_sessions`` until
    an operation finishes.
    """

    def __init__(self, history_limit: int = 10, *, max_sessions: int = 1000) -> None:
        """Initialize session storage limits.

        Args:
            history_limit: Maximum retained non-system messages per prepared
                conversation.
            max_sessions: Maximum number of idle retained sessions.

        Raises:
            ValueError: If either limit is less than one.
        """
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self.history_limit = history_limit
        self.max_sessions = max_sessions
        self._sessions: OrderedDict[str, list[ChatMessage]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._users: dict[str, int] = {}
        self._summarizing: set[str] = set()
        self._metadata_lock = asyncio.Lock()

    def _remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._locks.pop(session_id, None)
        self._users.pop(session_id, None)
        self._summarizing.discard(session_id)

    def _evict_idle(self, *, protected: str | None = None) -> None:
        while len(self._sessions) > self.max_sessions:
            victim = next(
                (
                    session_id
                    for session_id in self._sessions
                    if session_id != protected
                    and self._users.get(session_id, 0) == 0
                    and not self._locks.get(session_id, asyncio.Lock()).locked()
                    and session_id not in self._summarizing
                ),
                None,
            )
            if victim is None:
                return
            self._remove(victim)

    @asynccontextmanager
    async def _session_lock(self, session_id: str) -> AsyncIterator[None]:
        async with self._metadata_lock:
            lock = self._locks.setdefault(session_id, asyncio.Lock())
            self._users[session_id] = self._users.get(session_id, 0) + 1
        try:
            async with lock:
                async with self._metadata_lock:
                    self._sessions.setdefault(session_id, [])
                    self._sessions.move_to_end(session_id)
                    self._evict_idle(protected=session_id)
                yield
        finally:
            async with self._metadata_lock:
                users = self._users.get(session_id, 0) - 1
                if users > 0:
                    self._users[session_id] = users
                else:
                    self._users.pop(session_id, None)
                    if session_id not in self._sessions:
                        self._locks.pop(session_id, None)
                self._evict_idle()

    async def prepare(
        self,
        session_id: str,
        incoming: list[ChatMessage],
        summarizer: Summarizer | None = None,
    ) -> list[ChatMessage]:
        """Merge incoming messages with retained history for a request.

        System messages are preserved. Non-system history is trimmed to
        ``history_limit``; when a summarizer is supplied, older messages are
        summarized before trimming. The summarizer is awaited while this session's
        lock is held, preventing concurrent updates to the same session.

        Args:
            session_id: Session key whose history is read and updated.
            incoming: New messages to merge into the conversation.
            summarizer: Optional asynchronous function for compacting old
                non-system messages.

        Returns:
            A new list containing the prepared conversation.

        Raises:
            Exception: Any exception raised by ``summarizer`` is propagated.
        """
        async with self._session_lock(session_id):
            history = self._sessions[session_id]
            systems = [message for message in history + incoming if message.role == "system"]
            non_system = [message for message in history + incoming if message.role != "system"]
            if len(non_system) > self.history_limit and summarizer:
                self._summarizing.add(session_id)
                try:
                    old = non_system[: -self.history_limit + 1]
                    summary = await summarizer(old)
                    non_system = [
                        ChatMessage(role="system", content=f"Conversation summary: {summary}"),
                        *non_system[-self.history_limit + 1 :],
                    ]
                finally:
                    self._summarizing.discard(session_id)
            result = systems + non_system[-self.history_limit :]
            self._sessions[session_id] = result.copy()
            return result

    async def append(self, session_id: str, message: ChatMessage) -> None:
        """Append one message while serializing updates to the session.

        Args:
            session_id: Session key to create or update.
            message: Message appended without applying ``history_limit``.
        """
        async with self._session_lock(session_id):
            self._sessions[session_id].append(message)

    async def clear(self, session_id: str | None = None) -> int:
        """Remove idle session history.

        Args:
            session_id: Specific session to remove, or ``None`` to remove all
                currently idle sessions.

        Returns:
            Number of sessions removed. Active and summarizing sessions are
            skipped rather than cancelled.
        """
        async with self._metadata_lock:
            candidates = list(self._sessions) if session_id is None else [session_id]
            removed = 0
            for candidate in candidates:
                lock = self._locks.get(candidate)
                if (
                    candidate in self._sessions
                    and self._users.get(candidate, 0) == 0
                    and not (lock and lock.locked())
                    and candidate not in self._summarizing
                ):
                    self._remove(candidate)
                    removed += 1
            return removed

    async def count(self) -> int:
        """Return the current number of retained sessions."""
        async with self._metadata_lock:
            return len(self._sessions)
