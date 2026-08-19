from __future__ import annotations

import re
from typing import Any

from .client import AsyncAIMagic
from .dto import ChatMessage

_CODE_SYSTEM_PROMPT = (
    "You are an expert software engineer. Return only the requested source code. "
    "Do not include Markdown code fences, explanations, commentary, headings, or any "
    "text before or after the code."
)

_OUTER_CODE_FENCE = re.compile(r"\A[ \t]*```[^\r\n]*\r?\n(?P<code>[\s\S]*?)\r?\n[ \t]*```[ \t]*\Z")


def _normalize_session_id(session_id: str | int | None) -> str | None:
    """Convert a public session identifier to the client's string representation."""
    if session_id is None:
        return None
    if isinstance(session_id, bool) or not isinstance(session_id, (str, int)):
        raise TypeError("session_id must be a str, int, or None")
    return str(session_id)


def _strip_outer_code_fence(value: str) -> str:
    """Remove one complete outer Markdown fence while preserving unfenced code."""
    match = _OUTER_CODE_FENCE.fullmatch(value)
    return match.group("code") if match else value


async def chat(
    prompt: str,
    *,
    client: AsyncAIMagic | None = None,
    session_id: str | int | None = None,
    system: str | None = None,
    **kwargs: Any,
) -> str:
    """Return an assistant response for a text prompt.

    Args:
        prompt: User message sent to the model.
        client: Existing :class:`AsyncAIMagic` instance to reuse. The helper does
            not close a caller-owned client. If omitted, it creates and closes a
            temporary client, including when the request raises an exception.
        session_id: Optional string or integer conversation identifier. Integers
            are converted to decimal strings. Reusing an identifier with the same
            client enables that client's session history.
        system: Optional system instruction prepended to the user message.
        **kwargs: Additional completion options forwarded unchanged to
            ``client.chat.completions.create``, for example ``model``,
            ``temperature``, or ``max_tokens``.

    Returns:
        The text content of the first completion choice.

    Raises:
        TypeError: If ``session_id`` is not ``str``, ``int``, or ``None``.
        AIMagicError: If configuration, credentials, or a provider request fails.
        pydantic.ValidationError: If a forwarded completion option is invalid.
        ProviderError: If the provider returns no completion choices.
    """
    normalized_session_id = _normalize_session_id(session_id)
    owned = client is None
    client = client or AsyncAIMagic()
    messages: list[dict[str, Any] | ChatMessage] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        result = await client.chat.completions.create(
            messages=messages,
            session_id=normalized_session_id,
            **kwargs,
        )
        return result.choices[0].message.content
    finally:
        if owned:
            await client.aclose()


async def code(
    prompt: str,
    *,
    client: AsyncAIMagic | None = None,
    session_id: str | int | None = None,
    **kwargs: Any,
) -> str:
    """Generate source code without Markdown fences or explanatory prose.

    The model receives a strict system instruction to return source code only. If
    it nevertheless wraps the entire response in one Markdown code fence, that
    outer fence is removed. Unfenced output and fences embedded inside ordinary
    code are left unchanged.

    Args:
        prompt: Description of the source code to generate.
        client: Existing :class:`AsyncAIMagic` instance to reuse. It remains open
            after the call. If omitted, a temporary client is created and closed.
        session_id: Optional string or integer conversation identifier. Integers
            are safely normalized to decimal strings before forwarding.
        **kwargs: Additional completion options forwarded unchanged, such as
            ``model``, ``temperature``, or ``max_tokens``.

    Returns:
        Generated source code, with at most one complete outer Markdown code
        fence removed.

    Raises:
        TypeError: If ``session_id`` is not ``str``, ``int``, or ``None``.
        AIMagicError: If configuration, credentials, or a provider request fails.
        pydantic.ValidationError: If a forwarded completion option is invalid.
        ProviderError: If the provider returns no completion choices.
    """
    result = await chat(
        prompt,
        client=client,
        session_id=session_id,
        system=_CODE_SYSTEM_PROMPT,
        **kwargs,
    )
    return _strip_outer_code_fence(result)
