from __future__ import annotations

from typing import Any, Protocol

from .dto import ChatCompletion, ChatCompletionRequest, ChatMessage, Choice, Usage
from .exceptions import ProviderError


class ProviderAdapter(Protocol):
    """Convert normalized DTOs to and from a provider wire format."""

    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        """Build a provider request body for ``model``."""
        ...

    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion:
        """Parse provider JSON into a normalized completion."""
        ...


class OpenAIAdapter:
    """Adapt normalized chat DTOs to an OpenAI-compatible JSON contract."""

    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        """Build JSON, excluding the local-only session identifier."""
        payload = request.model_dump(exclude={"session_id"}, exclude_none=True)
        payload["model"] = model
        payload["messages"] = [message.model_dump() for message in request.messages]
        return payload

    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion:
        """Validate OpenAI-compatible JSON, filling a missing model value."""
        if not data.get("model"):
            data = {**data, "model": model}
        return ChatCompletion.model_validate(data)


def openai_to_gemini(messages: list[ChatMessage]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    systems = [m.content for m in messages if m.role == "system"]
    system = {"parts": [{"text": "\n\n".join(systems)}]} if systems else None
    contents = []
    for message in messages:
        if message.role == "system":
            continue
        role = "model" if message.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message.content}]})
    return system, contents


def gemini_to_openai(data: dict[str, Any], model: str) -> ChatCompletion:
    candidates = data.get("candidates")
    if not candidates:
        raise ProviderError("Gemini returned no candidates", status_code=200)
    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts)
    usage = data.get("usageMetadata", {})
    return ChatCompletion(
        model=model,
        choices=[
            Choice(
                message=ChatMessage(role="assistant", content=text),
                finish_reason=str(candidate.get("finishReason", "stop")).lower(),
            )
        ],
        usage=Usage(
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
        ),
    )


class GeminiAdapter:
    """Adapt normalized chat DTOs to the Gemini generate-content contract."""

    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        """Build Gemini contents, system instruction, and generation config."""
        system, contents = openai_to_gemini(request.messages)
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = system
        generation = {"temperature": request.temperature, "maxOutputTokens": request.max_tokens}
        payload["generationConfig"] = {key: value for key, value in generation.items() if value is not None}
        return payload

    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion:
        """Normalize the first Gemini candidate and token usage."""
        return gemini_to_openai(data, model)


class CohereAdapter:
    """Maps OpenAI chat messages to Cohere's native v1/chat contract."""

    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        """Build a Cohere v1/chat payload.

        Raises:
            ValueError: If the request has no non-system message.
        """
        systems = [message.content for message in request.messages if message.role == "system"]
        conversational = [message for message in request.messages if message.role != "system"]
        if not conversational:
            raise ValueError("Cohere requires at least one non-system message")
        current = conversational[-1]
        history = [
            {"role": "CHATBOT" if message.role == "assistant" else "USER", "message": message.content}
            for message in conversational[:-1]
        ]
        payload: dict[str, Any] = {"model": model, "message": current.content, "chat_history": history}
        if systems:
            payload["preamble"] = "\n\n".join(systems)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion:
        """Normalize Cohere text, finish reason, and available token usage."""
        meta = data.get("meta") or {}
        billed = meta.get("billed_units") or {}
        tokens = meta.get("tokens") or {}
        prompt = billed.get("input_tokens", tokens.get("input_tokens", 0))
        completion = billed.get("output_tokens", tokens.get("output_tokens", 0))
        return ChatCompletion(
            id=str(data.get("generation_id") or data.get("id") or ""),
            model=model,
            choices=[
                Choice(
                    message=ChatMessage(role="assistant", content=str(data.get("text", ""))),
                    finish_reason=str(data.get("finish_reason", "COMPLETE")).lower(),
                )
            ],
            usage=Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion),
        )
