# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Provider-aware credential routing across built-in OpenAI-compatible, Gemini, and Cohere adapters.
- Case-insensitive provider registry with registration for custom OpenAI-compatible endpoints.
- Concurrency-safe session history with per-session serialization, optional summarization, idle LRU eviction, and explicit clearing/counting.
- Structured logging for retry, fallback, credential-ban, and configuration events.
- Public API and lifecycle documentation across clients, providers, state managers, adapters, transport, settings, DTOs, and exceptions.

### Changed

- Credential rotation now respects model allow-lists and per-credential default models across providers.
- Retry and fallback behavior distinguishes transport failures, rate limits, authentication failures, and retryable provider errors.
- Environment configuration validates credential JSON and Gemini REST model identifiers.

### Fixed

- Caller-owned HTTP clients remain open when the ai_magic client is closed.
- Temporary high-level clients close their resources when requests fail.
- Session updates avoid concurrent history corruption and do not evict active sessions.
- Retry-After handling supports both delta-seconds and HTTP-date values.

## [0.1.0]

### Added

- Initial asynchronous Python client with `chat()`, `code()`, and `client.chat.completions.create()` APIs.
- Multi-key rotation, temporary credential bans, primary/fallback model handling, and bounded HTTP retries.
- Conversation history keyed by session identifier.
- Built-in configurations for Groq, NVIDIA, OpenRouter, Gemini, Together, Mistral, Cohere, and Hyperbolic.
- Pydantic request/response DTOs and a typed exception hierarchy.
- Typed package marker for downstream type checking.

[Unreleased]: https://github.com/Mydvyd/ai_magic/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Mydvyd/ai_magic/releases/tag/v0.1.0
