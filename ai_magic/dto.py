from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """Normalized chat message with a supported role and text content."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequest(BaseModel):
    """Provider-neutral request for a non-streaming chat completion."""

    model: str | None = None
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    session_id: str | None = None


class Choice(BaseModel):
    """One normalized completion choice returned by a provider."""

    index: int = 0
    message: ChatMessage
    finish_reason: str | None = "stop"


class Usage(BaseModel):
    """Provider-reported token counts, defaulting to zero when unavailable."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletion(BaseModel):
    """Normalized chat completion; unknown provider fields are preserved."""

    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)


JsonDict = dict[str, Any]
