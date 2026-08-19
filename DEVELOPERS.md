# ai_magic Developer Guide

## English

### Architecture and request flow

`AsyncAIMagic.chat.completions.create()` validates messages, asks `SessionManager` to merge session history, builds a provider-neutral `ChatCompletionRequest`, and invokes fallback routing. `CarouselProvider` acquires a compatible credential from `KeyManager`, resolves its `ProviderConfig` in `ProviderRegistry`, lets the adapter build provider JSON, and sends it through `AsyncTransport`. The adapter then normalizes provider JSON into `ChatCompletion`; a successful assistant message is appended to the session.

Flow: public API → DTO validation → session preparation → model fallback layer → credential carousel → registry/config → adapter build → transport/retry → adapter parse → normalized DTO → session append.

### Module responsibilities

- `api.py`: `chat()` and `code()`, `str | int` session normalization, completion argument forwarding, temporary-client lifecycle, and outer code-fence removal.
- `client.py`: OpenAI-like API surface, dependency injection, session integration, summarization, and provider-aware model fallback.
- `config.py`: validated `Settings`, environment loading, credential JSON parsing, and Gemini model-ID checks.
- `dto.py`: provider-neutral Pydantic request/response models.
- `providers.py`: `Provider` protocol, `ProviderConfig`, case-insensitive `ProviderRegistry`, built-ins, and `CarouselProvider`.
- `adapters.py`: `ProviderAdapter` protocol and OpenAI, Gemini, and Cohere wire-format adapters.
- `transport.py`: asynchronous HTTP, owned-client lifecycle, bounded retries, typed HTTP errors, and `Retry-After` parsing.
- `state.py`: immutable `Credential`, concurrency-safe `KeyManager`, and in-memory `SessionManager`.
- `_logging.py`: package logger named `ai_magic`; it intentionally installs no handler.
- `exceptions.py`: public typed error hierarchy.
- `__init__.py`: the authoritative public exports.

### ProviderRegistry and adapters

`ProviderRegistry` maps a case-insensitive provider name to immutable `ProviderConfig(name, base_url, endpoint, headers, adapter, auth)`. Registry injection avoids global state and makes tests deterministic. `ProviderAdapter` isolates wire formats:

```python
class ProviderAdapter(Protocol):
    def build(self, request: ChatCompletionRequest, model: str) -> dict[str, Any]: ...
    def parse(self, data: dict[str, Any], model: str) -> ChatCompletion: ...
```

The OpenAI adapter forwards normalized fields except local `session_id`; Gemini converts roles/system instructions and generation settings; Cohere v1 builds `message`, `chat_history`, and `preamble`. Routing and business layers must not parse provider-specific JSON.

### Transport and retry layering

`AsyncTransport` owns an internally created `httpx.AsyncClient`, but never closes a caller-supplied one. It maps HTTP 401/403 to `AuthenticationError`, 429 to `RateLimitError`, and other failures to `ProviderError`. `Retry-After` supports delta-seconds and HTTP-date.

Retries are deliberately layered:

1. Transport retries (`max_retries`, default `0`) handle network failures, HTTP 408, and 5xx with bounded exponential jitter.
2. HTTP 429 returns immediately to the carousel so it can ban that credential for `Retry-After` and rotate.
3. The carousel attempts each compatible credential at most once per call.
4. The client may try `fallback_model` after a retryable failure, subject to provider/model compatibility.

Authentication and ordinary 4xx errors are not retried. Keep transport retries low to avoid multiplying latency across keys and providers.

### Credential, model, and provider carousel

`Credential` contains `provider`, secret `key`, optional default `model`, model allow-list `models`, arbitrary `metadata`, and additional `headers`. Without explicit `model=`, a credential uses `model` or the first `models` item. With explicit `model=`, `KeyManager` filters credentials through `supports_model()`.

`KeyManager` protects round-robin position and ban state with `asyncio.Lock`. If all compatible credentials are banned, it computes the nearest unban while locked, releases the lock before sleeping, and respects the per-call `max_wait`/global `max_credential_wait` deadline. Retryable failures temporarily ban a credential; the secret itself is never logged.

### Provider-aware fallback

`model=None` is meaningful: it preserves each credential's default and permits safe cross-provider rotation. Do not replace it with `primary_model`. An explicit model is authoritative. `primary_model` remains the default for legacy key-list configuration and identifies the primary when explicitly requested. `fallback_model` is passed as an explicit model and therefore reaches only credentials that allow it. A request for another explicit model does not switch to the global fallback.

### SessionManager semantics

`SessionManager` stores in-process history only. System messages are retained; non-system history is trimmed to `history_limit`. If a summarizer is supplied, older messages are compacted into a system summary. `_summarizing` prevents unsafe eviction during compaction.

- Same-session operations are serialized by a per-session lock; different sessions may run concurrently.
- `max_sessions` is an idle-session limit with least-recently-used eviction.
- Active, queued, locked, or summarizing sessions are skipped, so count may temporarily exceed the limit.
- `await clear(session_id)` removes one idle session; `await clear()` removes all currently idle sessions and returns the number removed.
- `await count()` returns retained session count.
- Clearing never cancels active work. History is neither persistent nor shared between client instances.

### Logging and configuration

The library logs through `logging.getLogger("ai_magic")` and adds no handler. Applications control destination, formatting, and level:

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("ai_magic").setLevel(logging.DEBUG)
```

Debug/info records cover credential bans/waits, retry/fallback, summarization, and configuration warnings. Avoid custom HTTP logging that emits `Authorization` or query parameters.

### Validation and security

- Every configuration requires at least one non-empty credential.
- Gemini model IDs must be lowercase REST identifiers matching `[a-z0-9][a-z0-9.-]*`; syntax does not guarantee account access, so verify model availability for the account.
- `ProviderConfig.url()` permits only path-safe model characters (`[A-Za-z0-9][A-Za-z0-9._:@-]*`), preventing slash/segment path traversal.
- Never log `Credential.key`, bearer headers, Gemini query keys, complete sensitive URLs, or provider error payloads that may contain secrets.
- Keep real keys out of source, fixtures, documentation, exceptions, and build artifacts. Use fake values with `httpx.MockTransport` in tests.
- Generated code and provider responses remain untrusted input.

### Settings and environment reference

Direct `Settings(...)` construction is preferred for explicit application configuration; environment variables are optional through `Settings.from_env()` or a default `AsyncAIMagic()`.

| Setting | Environment | Default |
|---|---|---|
| `provider` | `AI_MAGIC_PROVIDER` | `groq` |
| `credentials` | `AI_MAGIC_CREDENTIALS` | JSON list / empty |
| `groq_api_keys` | `GROQ_API_KEYS`, fallback `GROQ_API_KEY` | empty |
| `gemini_api_keys` | `GEMINI_API_KEYS`, fallback `GEMINI_API_KEY` | empty |
| `groq_base_url` | — | `https://api.groq.com/openai/v1` |
| `gemini_base_url` | — | `https://generativelanguage.googleapis.com/v1beta` |
| `openrouter_referer` | `OPENROUTER_HTTP_REFERER` | `None` |
| `openrouter_title` | `OPENROUTER_X_TITLE` | `None` |
| `primary_model` | `AI_MAGIC_PRIMARY_MODEL` | `llama-3.3-70b-versatile` |
| `fallback_model` | `AI_MAGIC_FALLBACK_MODEL` | `llama-3.1-8b-instant` |
| `default_history_limit` | — | `10` |
| `max_sessions` | `AI_MAGIC_MAX_SESSIONS` | `1000` |
| `timeout` | `AI_MAGIC_TIMEOUT` | `60.0` |
| `max_retries` | `AI_MAGIC_MAX_RETRIES` | `0` |
| `max_credential_wait` | `AI_MAGIC_MAX_CREDENTIAL_WAIT` | `30.0` |

Explicit `from_env()` keyword overrides win. Invalid numeric environment values are logged and replaced by defaults; malformed `AI_MAGIC_CREDENTIALS` JSON is rejected.

### Adding an OpenAI-compatible provider

```python
registry.register_openai_compatible(
    "vendor",
    "https://api.vendor.example/v1",
    endpoint="/chat/completions",
    headers={"X-App": "ai_magic"},
)
```

Add a `Credential` with the same provider name and an explicit `models` allow-list. For a built-in provider, add its config to `builtin_provider_configs()` and test registry lookup, URL/auth, payload, parsing, and rotation.

### Adding a custom adapter provider

1. Implement `ProviderAdapter.build()` and `parse()`.
2. Construct `ProviderConfig` with the adapter, endpoint, headers, and `auth` (`bearer` or `query`).
3. Register it with `ProviderRegistry.register()`.
4. Inject the registry and a matching `KeyManager` into `AsyncAIMagic`.
5. Test payload construction, normalized parsing, URL/auth, failures, and provider-aware carousel behavior.

If a new authentication scheme is required, extend authentication centrally in the provider layer. Do not leak provider-only fields into common DTOs without a cross-provider need.

### Quality tools, CI, testing, build, release, and changelog

The test suite must use mocked transports and fake credentials; no network or real secrets. Regression coverage should include adapters, rotation, model allow-lists, cross-provider defaults, fallback, transport/status classification, `Retry-After`, bounded waits, concurrent sessions, summarization, LRU eviction, and clear semantics.

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pyright
python -m pytest -q
python -m compileall -q ai_magic tests
python -m build
```

GitHub Actions runs Ruff, Pyright, pytest, and package build on Python 3.12. Before release: keep `pyproject.toml` metadata and version consistent, update `CHANGELOG.md` in Keep a Changelog style, run all checks from a clean tree, inspect wheel/sdist contents for secrets, tag the version, and publish only reviewed artifacts. Follow Semantic Versioning; move relevant `Unreleased` entries into the versioned section.

---

## Русский

# Руководство разработчика ai_magic

## Архитектура и поток запроса

`AsyncAIMagic.chat.completions.create()` валидирует сообщения DTO, добавляет историю сессии и передаёт `ChatCompletionRequest` в fallback-слой. Затем `CarouselProvider` выбирает совместимый credential, получает конфигурацию из registry, adapter строит provider-specific payload, `AsyncTransport` выполняет HTTP-запрос, adapter нормализует ответ в `ChatCompletion`.

Поток: API → DTO → session → fallback → key carousel → registry/config → adapter → transport → adapter → DTO.

## Модули

- `api.py` — high-level helpers `chat()` и `code()`: формирование сообщений, нормализация `session_id: str | int`, проброс completion kwargs и управление жизненным циклом временного клиента. `code()` использует строгий system prompt «только исходный код» и снимает лишь полную внешнюю Markdown code fence; переданный клиент никогда не закрывается helper-слоем.
- `client.py` — публичный OpenAI-подобный API, dependency injection, session summary и model fallback.
- `config.py` — Pydantic Settings и чтение переменных окружения.
- `dto.py` — входные/выходные DTO, единый контракт независимо от provider.
- `providers.py` — registry, provider configs и межпровайдерская карусель.
- `adapters.py` — преобразование запросов и ответов; OpenAI, Gemini и Cohere adapters.
- `transport.py` — HTTP, timeout, ограниченные transport retries, классификация HTTP-ошибок и `Retry-After`.
- `state.py` — `Credential`, async-safe `KeyManager`, история и summary сессий.
- `exceptions.py` — публичная иерархия ошибок.
- `__init__.py` — публичные exports.

## DTO и adapters

Внутренний контракт — `ChatCompletionRequest`/`ChatCompletion`. Adapter реализует:

```python
class ProviderAdapter(Protocol):
    def build(self, request, model) -> dict: ...
    def parse(self, data, model) -> ChatCompletion: ...
```

Это изолирует нестандартные wire formats. Бизнес-слои не должны разбирать provider JSON.

## Registry и provider config

`ProviderRegistry` сопоставляет имя provider с `ProviderConfig`: base URL, endpoint, headers, auth и adapter. Registry передаётся через DI, поэтому тесты и приложения могут заменять конфигурацию без глобального состояния.

## Transport, retries и ошибки

`AsyncTransport` преобразует 401/403 в `AuthenticationError`, 429 в `RateLimitError`, остальные ошибки — в `ProviderError`. `Retry-After` поддерживает секунды и HTTP-date.

Retry layering ограничен: по умолчанию transport retries выключены (`max_retries=0`), а carousel пробует каждый совместимый credential не более одного раза. Если transport retries включены явно, 429 всё равно немедленно возвращается carousel, чтобы забанить ключ на `Retry-After`; transport повторяет только transport errors, 408 и 5xx.

## Key carousel и ожидание разблокировки

`Credential` содержит provider, key, default `model`, allow-list `models`, headers и metadata. Явный `model=` фильтрует credentials по allow-list; без явной модели используется default конкретного credential. Это не позволяет отправить Groq-модель в Gemini/Cohere при корректно заданном `models`.

`KeyManager` защищает индекс и bans через `asyncio.Lock`. Если все совместимые credentials забанены, он вычисляет ближайший `banned_until`, освобождает lock и только затем делает `asyncio.sleep`. `max_wait`/`max_credential_wait` задаёт deadline и исключает бесконечное ожидание.

## Session summary и fallback

`SessionManager` хранит историю по `session_id`, сохраняет system prompts и при превышении лимита вызывает summarizer. Защита `_summarizing` предотвращает рекурсивное суммирование. Summarizer без явной модели сохраняет ту же provider-aware семантику, что обычный запрос.

Отсутствующий `model` нельзя заменять на `Settings.primary_model`: он означает «использовать default выбранного credential» и сохраняет ротацию между providers. Явный `model` авторитетен и фильтрует credentials по allow-list. `primary_model` остаётся default для legacy key-list конфигурации и маркером primary при явном выборе; `fallback_model` после retryable ошибки передаётся как явная модель и потому применяется только к совместимым credentials. Запрос с другой явно выбранной моделью не переключается на глобальный fallback.

Для Gemini `Settings` отклоняет model IDs с uppercase/недопустимым REST-форматом. ID должен быть lowercase REST-идентификатором, например `gemini-2.0-flash`. Это проверяет только синтаксис: доступность точной модели необходимо отдельно проверить для конкретного аккаунта.

## Новая семантика SessionManager: LRU, лимит и очистка

Операции одной сессии сериализуются per-session lock, а разные сессии могут выполняться параллельно. `max_sessions` ограничивает число неактивных сохранённых сессий: при превышении удаляется least-recently-used сессия. Активные, ожидающие lock и суммируемые сессии не удаляются, поэтому счётчик может временно превышать лимит. `await client.sessions.clear(session_id)` удаляет одну неактивную сессию, `await client.sessions.clear()` — все доступные неактивные сессии; оба варианта возвращают число удалённых записей. `await client.sessions.count()` возвращает текущий размер. Очистка не отменяет активные запросы.

## Логирование и настройка

Библиотека использует logger `ai_magic` и не устанавливает handler. Конфигурация остаётся за приложением:

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("ai_magic").setLevel(logging.DEBUG)
```

Логи описывают bans/ожидание credentials, fallback, summary и ошибки числовых env-настроек. Нельзя подключать HTTP-логирование, раскрывающее `Authorization`, Gemini query key или полный URL с секретными параметрами.

## Settings и environment

Все параметры можно передать прямо в `Settings`; environment опционален через `Settings.from_env()`/`AsyncAIMagic()`. Поддерживаются `AI_MAGIC_PROVIDER`, JSON `AI_MAGIC_CREDENTIALS`, `GROQ_API_KEYS`/`GROQ_API_KEY`, `GEMINI_API_KEYS`/`GEMINI_API_KEY`, `AI_MAGIC_PRIMARY_MODEL`, `AI_MAGIC_FALLBACK_MODEL`, `AI_MAGIC_TIMEOUT`, `AI_MAGIC_MAX_RETRIES`, `AI_MAGIC_MAX_CREDENTIAL_WAIT`, `AI_MAGIC_MAX_SESSIONS`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE`. Python-only настройки: `groq_base_url`, `gemini_base_url`, `default_history_limit`. Значения по умолчанию соответственно: provider `groq`, primary `llama-3.3-70b-versatile`, fallback `llama-3.1-8b-instant`, timeout `60.0`, retries `0`, credential wait `30.0`, max sessions `1000`, history limit `10`. Явные overrides имеют приоритет; некорректный JSON credentials отклоняется.

## Dependency injection

`AsyncAIMagic` принимает `http_client`, `transport`, `provider`, `registry`, `credentials`, `sessions`. Для unit-тестов передавайте fake transport/provider; реальная сеть не нужна.

## Добавление OpenAI-compatible provider

```python
registry.register_openai_compatible(
    "vendor",
    "https://api.vendor.example/v1",
    headers={"X-App": "ai_magic"},
)
```

Добавьте credential с тем же provider и явным `models`. Если provider должен быть встроенным, добавьте `ProviderConfig` в `builtin_provider_configs()` и тест registry.

## Добавление нестандартного provider

1. Реализуйте adapter с `build()` и `parse()`.
2. Добавьте `ProviderConfig(name, base_url, endpoint, adapter, auth)`.
3. Если auth отличается от bearer/query, расширьте auth-ветку централизованно.
4. Добавьте unit-тесты payload, parsing, URL/auth и carousel behavior.
5. Не протаскивайте provider-specific поля в общие DTO без необходимости.

## Безопасность ключей

- Не логируйте `Credential.key`, Authorization и query key.
- Не храните реальные ключи в репозитории, fixtures и exception messages.
- Используйте environment/secret manager.
- В тестах применяйте фиктивные значения и `httpx.MockTransport`.
- При добавлении telemetry редактируйте headers и URL query.

## Тестирование

Все тесты должны работать без сети. Основные регрессии: key rotation, model fallback, provider-aware selection, ожидание unban, deadline, `Retry-After`, cross-provider rotation, adapters и session history.

Команды из корня проекта:

```bash
python -m compileall -q ai_magic tests
python -c "import ai_magic; from ai_magic import AsyncAIMagic"
python -m pytest -q
```

Перед merge все три команды должны завершаться успешно на Python 3.12.
