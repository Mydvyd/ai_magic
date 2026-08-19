# ai_magic

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/Mydvyd/ai_magic/actions/workflows/ci.yml/badge.svg)](https://github.com/Mydvyd/ai_magic/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## English

### Overview

`ai_magic` is an asynchronous Python 3.12+ client that exposes one OpenAI-like API for several AI providers. It supports provider-aware credential and model rotation, bounded retries and fallback, and in-memory conversation sessions. The project is alpha software, so interfaces may change.

### Features

- High-level `chat()` and `code()` helpers plus `client.chat.completions.create()`.
- Built-in OpenAI-compatible, Gemini, and Cohere adapters.
- Round-robin rotation across keys and providers, with model allow-lists.
- Primary/fallback model handling without sending a model to an incompatible provider.
- Retry-After-aware temporary credential bans and bounded waiting.
- Concurrency-safe session history, summarization, LRU eviction, and explicit clearing.
- Typed DTOs, exceptions, dependency injection, and custom provider registration.

### Installation

From Git:

```bash
python -m pip install "git+https://github.com/Mydvyd/ai_magic.git"
```

For local development:

```bash
git clone https://github.com/Mydvyd/ai_magic.git
cd ai_magic
python -m pip install -e ".[dev]"
```

Using a virtual environment is recommended.

### Copy-paste quick start: configure everything in Python

No shell exports are required. Replace placeholders at runtime and never commit a real key.

```python
import asyncio

from ai_magic import AsyncAIMagic, Settings, chat

settings = Settings(
    credentials=[
        {
            "provider": "groq",
            "key": "PASTE_GROQ_KEY_HERE",
            "models": ["llama-3.3-70b-versatile"],
        }
    ],
    primary_model="llama-3.3-70b-versatile",
    fallback_model="llama-3.3-70b-versatile",
)


async def main() -> None:
    async with AsyncAIMagic(settings) as client:
        print(await chat("Explain async/await briefly", client=client))


asyncio.run(main())
```

### Chat and code

```python
from ai_magic import chat, code

answer = await chat(
    "Explain event loops in three sentences",
    client=client,
    temperature=0.2,
    max_tokens=200,
)
source = await code(
    "Write a typed Python add(a, b) function",
    client=client,
    temperature=0,
    max_tokens=200,
)
```

`code()` requests source code only and removes one complete outer Markdown fence if present. Review generated code; never execute untrusted output without isolation.

### Low-level API

```python
response = await client.chat.completions.create(
    messages=[
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "What is an event loop?"},
    ],
    model="llama-3.3-70b-versatile",
    session_id="user-42",
    temperature=0.2,
    max_tokens=200,
)
print(response.choices[0].message.content)
```

The method returns `ChatCompletion`. Supported request fields are `messages`, `model`, `session_id`, `temperature`, `max_tokens`, and `stream`; streaming is not implemented, so do not set `stream=True`.

### Providers and endpoints

| `provider` | Base URL | Adapter |
|---|---|---|
| `groq` | `https://api.groq.com/openai/v1` | OpenAI-compatible |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible |
| `openrouter` | `https://openrouter.ai/api/v1` | OpenAI-compatible |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | Gemini |
| `together` | `https://api.together.xyz/v1` | OpenAI-compatible |
| `mistral` | `https://api.mistral.ai/v1` | OpenAI-compatible |
| `cohere` | `https://api.cohere.ai/v1` | Cohere v1 chat |
| `hyperbolic` | `https://api.hyperbolic.xyz/v1` | OpenAI-compatible |

Each provider requires its own key. Gemini uses `/models/{model}:generateContent`; Cohere uses `/chat`; the others default to `/chat/completions`.

### Credentials, models, and rotation

A single key:

```python
settings = Settings(
    credentials=[
        {
            "provider": "groq",
            "key": "KEY_PLACEHOLDER",
            "models": ["llama-3.3-70b-versatile"],
        }
    ]
)
```

Multiple keys/providers:

```python
settings = Settings(
    credentials=[
        {"provider": "groq", "key": "GROQ_KEY_1", "models": ["llama-3.3-70b-versatile"]},
        {"provider": "groq", "key": "GROQ_KEY_2", "models": ["llama-3.3-70b-versatile"]},
        {"provider": "gemini", "key": "GEMINI_KEY", "models": ["gemini-2.0-flash"]},
    ]
)
```

Without `model=`, each credential uses `model` or the first entry in `models`. With explicit `model=`, only credentials whose allow-list supports it participate. A credential with neither field accepts any explicit model for backward compatibility; declare `models` for safe routing. Retryable transport errors, HTTP 429, and HTTP 5xx temporarily ban a credential and advance the carousel. `Retry-After` controls the ban where provided.

**Gemini model IDs must be lowercase REST IDs**, for example `gemini-2.0-flash`. Syntax validation does not prove access: check that the exact model is currently available to your Gemini account before running an example.

### Sessions, context, LRU, and clear

A repeated `session_id` retains context only in the same `AsyncAIMagic` instance and process. Calls for the same session are serialized; different sessions can proceed concurrently. Older messages are summarized when the history limit is exceeded. `max_sessions` limits idle retained sessions with least-recently-used eviction. Active/summarizing sessions are not evicted, so the count can temporarily exceed the limit.

```python
await chat("My name is Misha", client=client, session_id="42")
answer = await chat("What is my name?", client=client, session_id="42")
removed = await client.sessions.clear("42")  # clear one idle session
removed_all = await client.sessions.clear()  # clear all currently idle sessions
count = await client.sessions.count()
```

High-level helpers accept `str | int | None`; the low-level method accepts `str | None`.

### Settings and optional environment variables

Direct Python configuration is enough. `Settings.from_env()` and `AsyncAIMagic()` make environment configuration optional.

| Python setting | Environment variable | Default |
|---|---|---|
| `provider` | `AI_MAGIC_PROVIDER` | `groq` |
| `credentials` | `AI_MAGIC_CREDENTIALS` | JSON list / empty |
| `groq_api_keys` | `GROQ_API_KEYS` or `GROQ_API_KEY` | comma-separated / empty |
| `gemini_api_keys` | `GEMINI_API_KEYS` or `GEMINI_API_KEY` | comma-separated / empty |
| `primary_model` | `AI_MAGIC_PRIMARY_MODEL` | `llama-3.3-70b-versatile` |
| `fallback_model` | `AI_MAGIC_FALLBACK_MODEL` | `llama-3.1-8b-instant` |
| `timeout` | `AI_MAGIC_TIMEOUT` | `60.0` |
| `max_retries` | `AI_MAGIC_MAX_RETRIES` | `0` |
| `max_credential_wait` | `AI_MAGIC_MAX_CREDENTIAL_WAIT` | `30.0` |
| `max_sessions` | `AI_MAGIC_MAX_SESSIONS` | `1000` |
| `openrouter_referer` | `OPENROUTER_HTTP_REFERER` | `None` |
| `openrouter_title` | `OPENROUTER_X_TITLE` | `None` |

`default_history_limit` (default `10`), `groq_base_url`, and `gemini_base_url` are Python settings without environment mappings. Explicit arguments to `Settings.from_env()` override environment values. `AI_MAGIC_CREDENTIALS` must be valid JSON.

### OpenRouter headers

```python
settings = Settings(
    credentials=[{"provider": "openrouter", "key": "KEY", "models": ["vendor/model"]}],
    openrouter_referer="https://your-app.example",
    openrouter_title="Your App",
)
```

These become `HTTP-Referer` and `X-Title`. Per-credential `headers` are also supported.

### Custom providers

For an OpenAI-compatible endpoint, use the exported registry and credential classes:

```python
from ai_magic import AsyncAIMagic, Credential, KeyManager, ProviderRegistry, Settings

registry = ProviderRegistry()
registry.register_openai_compatible("vendor", "https://api.vendor.example/v1")
credentials = KeyManager([Credential("vendor", "KEY", models=("model-a",))])
settings = Settings(credentials=[{"provider": "placeholder", "key": "placeholder"}])
client = AsyncAIMagic(settings, registry=registry, credentials=credentials)
```

For another wire format, implement an adapter with `build(request, model)` and `parse(data, model)`, create an exported `ProviderConfig`, and register it. See [DEVELOPERS.md](DEVELOPERS.md).

### Errors, retries, and logging

Public exceptions are `AIMagicError`, `ConfigurationError`, `ProviderError`, `RateLimitError`, `AuthenticationError`, and `AllKeysUnavailableError`. Transport retries cover network errors, HTTP 408, and 5xx. HTTP 429 returns immediately to the carousel for key rotation. Authentication errors are not retried. `fallback_model` is attempted only after retryable failures, and only through compatible credentials.

The library uses the `ai_magic` logger and installs no handler:

```python
import logging

logging.basicConfig(level=logging.INFO)
logging.getLogger("ai_magic").setLevel(logging.DEBUG)
```

Debug logs describe retries, bans, fallback, summarization, and invalid numeric environment values. Do not attach handlers that expose sensitive request headers or query strings.

### Security

- Never commit or log keys; use environment variables or a secret manager in production.
- Authorization headers and Gemini query keys are not logged by the library.
- Provider model IDs are restricted to path-safe characters, preventing model path traversal.
- Gemini IDs additionally require lowercase REST syntax.
- Review generated text/code and treat provider responses as untrusted data.

### Testing

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pyright
python -m pytest -q
python -m compileall -q ai_magic tests
python -m build
```

Tests use fake credentials and mocked transports; no live provider keys or network calls are required. CI runs Ruff, Pyright, pytest, and build on Python 3.12.

### Project links

- [Developer guide](DEVELOPERS.md)
- [Changelog](CHANGELOG.md)
- [MIT License](LICENSE)
- [Repository](https://github.com/Mydvyd/ai_magic)

---

## Русский перевод

`ai_magic` — асинхронный клиент для Python 3.12+ с единым OpenAI-подобным API к нескольким AI-провайдерам.

Возможности:

- вызовы через `chat()`, `code()` и `client.chat.completions.create()`;
- ротация нескольких API-ключей и совместимых провайдеров;
- переход к следующему credential при transport error, HTTP 429 и 5xx;
- primary/fallback-модели;
- история диалога по `session_id`;
- встроенные адаптеры OpenAI-compatible API, Gemini и Cohere;
- регистрация собственных OpenAI-compatible провайдеров.

> Проект находится на стадии alpha. Интерфейс может изменяться.

## Требования

- Python 3.12 или новее;
- API key для каждого используемого провайдера;
- сетевой доступ к API выбранных провайдеров.

## Установка

### Напрямую из GitHub

```bash
python -m pip install "git+https://github.com/Mydvyd/ai_magic.git"
```

### Editable-установка для разработки

```bash
git clone https://github.com/Mydvyd/ai_magic.git
cd ai_magic
python -m pip install -e ".[test]"
```

### Локальная установка через `requirements.txt`

```bash
git clone https://github.com/Mydvyd/ai_magic.git
cd ai_magic
python -m pip install -r requirements.txt
python -m pip install -e .
```

Рекомендуется выполнять установку в виртуальном окружении.

## Встроенные провайдеры

| `provider` | Base URL | Формат API |
|---|---|---|
| `groq` | `https://api.groq.com/openai/v1` | OpenAI-compatible |
| `nvidia` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible |
| `openrouter` | `https://openrouter.ai/api/v1` | OpenAI-compatible |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | Gemini adapter |
| `together` | `https://api.together.xyz/v1` | OpenAI-compatible |
| `mistral` | `https://api.mistral.ai/v1` | OpenAI-compatible |
| `cohere` | `https://api.cohere.ai/v1` | Cohere adapter |
| `hyperbolic` | `https://api.hyperbolic.xyz/v1` | OpenAI-compatible |

**Для каждого провайдера нужен собственный API key**, полученный у этого провайдера. Ключ одного сервиса нельзя использовать для другого.

## Быстрый старт: вся настройка в Python

Ниже один полностью копируемый файл. Замените строковый placeholder ключом либо используйте безопасное чтение через `os.environ` (показано далее). Идентификаторы Gemini должны быть в lowercase и с дефисами. `gemini-2.0-flash` и `gemini-2.0-flash-lite` здесь иллюстрируют требуемый формат: перед запуском сверьте реальные доступные ID со списком моделей вашего аккаунта.


```python
import asyncio

from ai_magic import AsyncAIMagic, Settings, chat, code


settings = Settings(
    credentials=[
        {
            "provider": "gemini",
            "key": "PASTE_GEMINI_KEY_HERE",  # не коммитьте реальный ключ
            "models": ["gemini-2.0-flash", "gemini-2.0-flash-lite"],
        }
    ],
    primary_model="gemini-2.0-flash",
    fallback_model="gemini-2.0-flash-lite",
    timeout=60,
    max_retries=0,
    max_credential_wait=30,
)


async def main() -> None:
    async with AsyncAIMagic(settings) as client:
        answer = await chat(
            "Объясни async/await в трёх предложениях",
            client=client,
            temperature=0.2,
            max_tokens=200,
        )
        print(answer)

        source = await code(
            "Напиши Python-функцию add(a, b) с type hints",
            client=client,
            temperature=0,
            max_tokens=200,
        )
        print(source)

        response = await client.chat.completions.create(
            messages=[{"role": "user", "content": "Что такое event loop?"}],
            model="gemini-2.0-flash",
        )
        print(response.choices[0].message.content)


asyncio.run(main())
```

Именно `await chat("...", client=client)` отвечает на вопрос «как вызвать chat». `async with` закрывает HTTP-клиент автоматически. Если создать `client = AsyncAIMagic(settings)` без контекстного менеджера, обязательно вызовите `await client.aclose()` в `finally`.

## Короткие Python-конфиги

### Один provider: Gemini

```python
from ai_magic import Settings

settings = Settings(
    credentials=[{"provider": "gemini", "key": "PASTE_KEY_HERE", "models": ["gemini-2.0-flash"]}],
    primary_model="gemini-2.0-flash",
    fallback_model="gemini-2.0-flash",
)
```

Модель из примера может быть недоступна вашему аккаунту: используйте точный lowercase REST ID из доступного вам списка Gemini models.

### Один provider: Groq

```python
from ai_magic import Settings

settings = Settings(
    credentials=[{"provider": "groq", "key": "PASTE_KEY_HERE", "models": ["llama-3.3-70b-versatile"]}],
    primary_model="llama-3.3-70b-versatile",
    fallback_model="llama-3.3-70b-versatile",
)
```

### Несколько ключей

```python
from ai_magic import Settings

settings = Settings(
    credentials=[
        {"provider": "groq", "key": "KEY_1", "models": ["llama-3.3-70b-versatile"]},
        {"provider": "groq", "key": "KEY_2", "models": ["llama-3.3-70b-versatile"]},
    ]
)
```

`model` задаёт default credential. Если `model` отсутствует, default — первый элемент `models`.

При HTTP 429, 5xx или transport error credential временно блокируется, и карусель пробует следующий совместимый credential. Заголовок `Retry-After` определяет срок блокировки. Ожидание ближайшей разблокировки ограничено `max_credential_wait` (по умолчанию 30 секунд).

### Несколько providers

Без `model=` каждый credential использует собственную default-модель. Поэтому high-level `chat()`/`code()` и low-level вызов могут после retryable error перейти между providers, не отправляя Gemini ID в Groq или наоборот.

```python
from ai_magic import Settings

settings = Settings(
    credentials=[
        {"provider": "gemini", "key": "GEMINI_KEY", "models": ["gemini-2.0-flash"]},
        {"provider": "groq", "key": "GROQ_KEY", "models": ["llama-3.3-70b-versatile"]},
    ],
    # Глобальный fallback применяется только к credential, который объявил его в models.
    primary_model="llama-3.3-70b-versatile",
    fallback_model="llama-3.3-70b-versatile",
)
```

Явный `model="..."` — строгий выбор: участвуют только credentials, чьи `models`/`model` разрешают этот ID. Credential без `model` и `models` для обратной совместимости принимает любой явный ID; для безопасной provider-aware маршрутизации всегда задавайте allow-list.

`primary_model` сохраняет роль default для legacy-настройки `groq_api_keys`/`gemini_api_keys` и обозначает primary при явном запросе этой модели. Он больше не подменяет отсутствующий `model`. `fallback_model` пробуется после 429, 5xx, transport error или отсутствия доступного credential и также проходит строгую фильтрацию совместимости.

### Параметры и `session_id`

```python
answer = await chat(
    "Продолжи разговор",
    client=client,
    session_id="user-42",
    model="gemini-2.0-flash",  # опустите для ротации по default-моделям
    temperature=0.2,
    max_tokens=300,
)
```

### Безопасное чтение ключей

```python
import os
from ai_magic import Settings

settings = Settings(
    credentials=[
        {
            "provider": "gemini",
            "key": os.environ["GEMINI_API_KEY"],
            "models": ["gemini-2.0-flash"],
        }
    ]
)
```

Это рекомендуемый способ не хранить секрет в исходнике; shell-команды для библиотеки не обязательны. Для OpenRouter поля `openrouter_referer`, `openrouter_title` и credential `headers` также передаются прямо в `Settings`.

## High-level API: `chat()`

`chat()` принимает `prompt`, необязательные `client`, `session_id`, `system` и параметры completion. Возвращается текст первого ответа.

```python
import asyncio

from ai_magic import chat


async def main() -> None:
    answer = await chat(
        "Объясни async/await в трёх предложениях",
        system="Отвечай кратко и по-русски.",
        model="llama-3.3-70b-versatile",
        temperature=0.2,
        max_tokens=200,
    )
    print(answer)


asyncio.run(main())
```

Без `client=` helper создаёт временный клиент и закрывает его после запроса. Для серии запросов переиспользуйте клиент:

```python
import asyncio

from ai_magic import AsyncAIMagic, chat


async def main() -> None:
    async with AsyncAIMagic() as client:
        first = await chat("Представься", client=client)
        second = await chat("Назови один факт о Python", client=client)
        print(first)
        print(second)


asyncio.run(main())
```

## High-level API: `code()`

`code()` отправляет строгую system-инструкцию вернуть только исходный код. Если весь ответ обёрнут в один внешний Markdown code fence, helper снимает эту обёртку. Ответ модели всё равно необходимо проверять.

```python
import asyncio

from ai_magic import code


async def main() -> None:
    source = await code(
        "Напиши Python-функцию add(a, b) с type hints",
        model="llama-3.3-70b-versatile",
        temperature=0,
        max_tokens=200,
    )
    print(source)


asyncio.run(main())
```

**Не выполняйте недоверенный сгенерированный код** без ручного аудита и изоляции.

## Low-level API

`client.chat.completions.create()` возвращает экспортируемый объект `ChatCompletion`, а не строку:

```python
import asyncio

from ai_magic import AsyncAIMagic


async def main() -> None:
    async with AsyncAIMagic() as client:
        response = await client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Отвечай по-русски."},
                {"role": "user", "content": "Что такое event loop?"},
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.2,
            max_tokens=200,
            session_id="user-42",
        )
        print(response.choices[0].message.content)
        print(response.model)
        print(response.usage.total_tokens)


asyncio.run(main())
```

Поддерживаемые completion-поля: `messages`, `model`, `session_id`, `temperature`, `max_tokens` и `stream`. Потоковые ответы сейчас не реализованы: не устанавливайте `stream=True`.

## Сессии: `session_id`

Одинаковый `session_id` при повторном использовании **того же экземпляра** `AsyncAIMagic` сохраняет контекст разговора в памяти:

```python
import asyncio

from ai_magic import AsyncAIMagic, chat


async def main() -> None:
    async with AsyncAIMagic() as client:
        await chat(
            "Меня зовут Михаил",
            client=client,
            session_id=42,
            system="Отвечай кратко.",
        )
        answer = await chat(
            "Как меня зовут?",
            client=client,
            session_id=42,
        )
        print(answer)


asyncio.run(main())
```

В `chat()` и `code()` допустим `session_id: str | int | None`; целое число преобразуется в строку. Low-level метод принимает `str | None`. История не сохраняется между процессами и не переносится в новый клиент.

## Собственный OpenAI-compatible provider

Используйте экспортируемые `ProviderRegistry`, `Credential` и `KeyManager`:

```python
import asyncio

from ai_magic import (
    AsyncAIMagic,
    Credential,
    KeyManager,
    ProviderRegistry,
    Settings,
)


async def main() -> None:
    registry = ProviderRegistry()
    registry.register_openai_compatible(
        "my-provider",
        "https://api.example.com/v1",
        headers={"X-Custom-Header": "value"},
    )
    credentials = KeyManager(
        [
            Credential(
                provider="my-provider",
                key="secret",
                models=("model-a", "model-b"),
            )
        ]
    )
    settings = Settings(
        credentials=[{"provider": "placeholder", "key": "placeholder"}],
        primary_model="model-a",
        fallback_model="model-b",
    )

    async with AsyncAIMagic(
        settings=settings,
        registry=registry,
        credentials=credentials,
    ) as client:
        response = await client.chat.completions.create(
            messages=[{"role": "user", "content": "Hello"}],
            model="model-a",
        )
        print(response.choices[0].message.content)


asyncio.run(main())
```

Для нестандартного wire format нужен собственный adapter и `ProviderConfig`; архитектура описана в [DEVELOPERS.md](DEVELOPERS.md).

## Безопасность API-ключей

- храните ключи в переменных окружения или secret manager;
- не записывайте реальные ключи в README, код, тесты, логи и исключения;
- не коммитьте `.env` и локальные конфигурации с секретами;
- при утечке немедленно отзовите ключ у провайдера и выпустите новый;
- используйте отдельные ключи с минимально необходимыми лимитами и правами.

## Тестирование

Из корня репозитория:

```bash
python -m pip install -e ".[test]"
python -m compileall -q ai_magic tests
python -c "import ai_magic; from ai_magic import AsyncAIMagic"
python -m pytest -q
```

Тесты проекта выполняются без реальных API-ключей и без сетевых запросов.

## Документация и лицензия

- [Руководство разработчика](DEVELOPERS.md)
- [Лицензия MIT](LICENSE)
- [Официальный репозиторий](https://github.com/Mydvyd/ai_magic)
