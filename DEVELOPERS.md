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

`SessionManager` хранит историю по `session_id`, сохраняет system prompts и при превышении лимита вызывает summarizer. Защита `_summarizing` предотвращает рекурсивное суммирование.

Fallback выполняется только после retryable ошибки primary model. Fallback model передаётся как явный `model=` и потому применяется принудительно, но только к совместимым credentials.

## Dependency injection

`AsyncAIMagic` принимает `http_client`, `transport`, `provider`, `registry`, `credentials`, `sessions`. Для unit-тестов передавайте fake transport/provider; реальная сеть не нужна.

## Добавление OpenAI-compatible provider

```python
registry.register_openai_compatible(
    "vendor", "https://api.vendor.example/v1",
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
