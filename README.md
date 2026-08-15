# ai_magic

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

## Настройка

### Переменные окружения

Основной способ настройки — `AI_MAGIC_CREDENTIALS`: JSON-массив credentials с полями `provider`, `key`, необязательными `model`, `models`, `headers` и `metadata`.

Linux/macOS:

```bash
export AI_MAGIC_CREDENTIALS='[{"provider":"groq","key":"gsk_...","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]}]'
```

PowerShell:

```powershell
$env:AI_MAGIC_CREDENTIALS='[{"provider":"groq","key":"gsk_...","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]}]'
```

Для обратной совместимости одиночный provider также можно настроить так:

```bash
export AI_MAGIC_PROVIDER='groq'
export GROQ_API_KEY='gsk_...'
```

Поддерживаются `GROQ_API_KEY`/`GROQ_API_KEYS` и `GEMINI_API_KEY`/`GEMINI_API_KEYS`; варианты во множественном числе принимают ключи через запятую. Для новых конфигураций предпочтителен `AI_MAGIC_CREDENTIALS`.

### Настройка прямо в Python

```python
from ai_magic import AsyncAIMagic, Settings

settings = Settings(
    credentials=[
        {
            "provider": "groq",
            "key": "gsk_...",
            "models": [
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
            ],
        }
    ],
    primary_model="llama-3.3-70b-versatile",
    fallback_model="llama-3.1-8b-instant",
    timeout=60.0,
    max_retries=0,
    max_credential_wait=30.0,
)

client = AsyncAIMagic(settings)
```

Закрывайте созданный клиент через `async with` или `await client.aclose()`.

### Один provider

```bash
export AI_MAGIC_CREDENTIALS='[{"provider":"gemini","key":"...","models":["gemini-2.0-flash"]}]'
```

При этом задайте соответствующие модели в Python:

```python
settings = Settings.from_env(
    primary_model="gemini-2.0-flash",
    fallback_model="gemini-2.0-flash",
)
```

### Несколько ключей и моделей

```bash
export AI_MAGIC_CREDENTIALS='[
  {"provider":"groq","key":"key-1","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]},
  {"provider":"groq","key":"key-2","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]}
]'
```

`model` задаёт default-модель credential. `models` задаёт список разрешённых моделей, а его первый элемент считается default, если `model` отсутствует. В публичном `AsyncAIMagic` запрос без `model=` использует `Settings.primary_model`; fallback использует `Settings.fallback_model`. Поэтому эти модели должны быть разрешены хотя бы одним credential.

При HTTP 429, 5xx или transport error credential временно блокируется, и карусель пробует следующий совместимый credential. Заголовок `Retry-After` определяет срок блокировки. Ожидание ближайшей разблокировки ограничено `max_credential_wait` (по умолчанию 30 секунд).

### Межпровайдерская ротация

Ротация между провайдерами работает только для одной и той же модели, которую действительно поддерживают все участвующие провайдеры. Укажите её в `models` каждого credential:

```bash
export AI_MAGIC_CREDENTIALS='[
  {"provider":"provider-a","key":"key-a","models":["shared-model"]},
  {"provider":"provider-b","key":"key-b","models":["shared-model"]}
]'
```

```python
from ai_magic import Settings

settings = Settings.from_env(
    primary_model="shared-model",
    fallback_model="shared-model",
)
```

Не объявляйте модель совместимой только ради ротации: имена и доступность моделей определяются самими провайдерами. Credential без `model` и `models` принимает любую явно выбранную модель, поэтому ответственность за совместимость в таком случае лежит на приложении.

### Заголовки OpenRouter

```bash
export OPENROUTER_HTTP_REFERER='https://example.com'
export OPENROUTER_X_TITLE='My Application'
```

То же можно настроить прямо в Python:

```python
settings = Settings.from_env(
    openrouter_referer="https://example.com",
    openrouter_title="My Application",
)
```

Произвольные заголовки конкретного credential задаются полем `headers` в `AI_MAGIC_CREDENTIALS` или `Credential.headers`.

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
