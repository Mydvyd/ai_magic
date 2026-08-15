from __future__ import annotations

from typing import Any, Protocol

from .dto import ChatCompletion, ChatCompletionRequest, ChatMessage, Choice, Usage


class ProviderAdapter(Protocol):
    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]: ...
    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion: ...


class OpenAIAdapter:
    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        payload = request.model_dump(exclude={"session_id"}, exclude_none=True)
        payload["model"] = model
        payload["messages"] = [message.model_dump() for message in request.messages]
        return payload

    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion:
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
    candidate = (data.get("candidates") or [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts)
    usage = data.get("usageMetadata", {})
    return ChatCompletion(
        model=model,
        choices=[Choice(message=ChatMessage(role="assistant", content=text), finish_reason=str(candidate.get("finishReason", "stop")).lower())],
        usage=Usage(
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            total_tokens=usage.get("totalTokenCount", 0),
        ),
    )


class GeminiAdapter:
    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        system, contents = openai_to_gemini(request.messages)
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = system
        generation = {"temperature": request.temperature, "maxOutputTokens": request.max_tokens}
        payload["generationConfig"] = {key: value for key, value in generation.items() if value is not None}
        return payload

    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion:
        return gemini_to_openai(data, model)


class CohereAdapter:
    """Maps OpenAI chat messages to Cohere's native v1/chat contract."""

    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]:
        systems = [message.content for message in request.messages if message.role == "system"]
        conversational = [message for message in request.messages if message.role != "system"]
        current = conversational[-1] if conversational else ChatMessage(role="user", content="")
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
        meta = data.get("meta") or {}
        billed = meta.get("billed_units") or {}
        tokens = meta.get("tokens") or {}
        prompt = billed.get("input_tokens", tokens.get("input_tokens", 0))
        completion = billed.get("output_tokens", tokens.get("output_tokens", 0))
        return ChatCompletion(
            id=str(data.get("generation_id") or data.get("id") or ""),
            model=model,
            choices=[Choice(message=ChatMessage(role="assistant", content=str(data.get("text", ""))), finish_reason=str(data.get("finish_reason", "COMPLETE")).lower())],
            usage=Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion),
        )
