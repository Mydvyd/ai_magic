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

## Быстрый старт: вся настройка в Python

Ниже один полностью копируемый файл. Замените строковый placeholder ключом либо используйте безопасное чтение через `os.environ` (показано далее). Идентификаторы Gemini должны быть в lowercase и с дефисами. `gemini-3.7-flash` и `gemini-3.6-flash` здесь иллюстрируют требуемый формат: перед запуском сверьте реальные доступные ID со списком моделей вашего аккаунта.


```python
import asyncio

from ai_magic import AsyncAIMagic, Settings, chat, code


settings = Settings(
    credentials=[{
        "provider": "gemini",
        "key": "PASTE_GEMINI_KEY_HERE",  # не коммитьте реальный ключ
        "models": ["gemini-3.7-flash", "gemini-3.6-flash"],
    }],
    primary_model="gemini-3.7-flash",
    fallback_model="gemini-3.6-flash",
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
            model="gemini-3.7-flash",
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
    credentials=[{"provider": "gemini", "key": "PASTE_KEY_HERE", "models": ["gemini-3.7-flash"]}],
    primary_model="gemini-3.7-flash",
    fallback_model="gemini-3.7-flash",
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

settings = Settings(credentials=[
    {"provider": "groq", "key": "KEY_1", "models": ["llama-3.3-70b-versatile"]},
    {"provider": "groq", "key": "KEY_2", "models": ["llama-3.3-70b-versatile"]},
])
```

`model` задаёт default credential. Если `model` отсутствует, default — первый элемент `models`.

При HTTP 429, 5xx или transport error credential временно блокируется, и карусель пробует следующий совместимый credential. Заголовок `Retry-After` определяет срок блокировки. Ожидание ближайшей разблокировки ограничено `max_credential_wait` (по умолчанию 30 секунд).

### Несколько providers

Без `model=` каждый credential использует собственную default-модель. Поэтому high-level `chat()`/`code()` и low-level вызов могут после retryable error перейти между providers, не отправляя Gemini ID в Groq или наоборот.

```python
from ai_magic import Settings

settings = Settings(
    credentials=[
        {"provider": "gemini", "key": "GEMINI_KEY", "models": ["gemini-3.7-flash"]},
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
    model="gemini-3.7-flash",  # опустите для ротации по default-моделям
    temperature=0.2,
    max_tokens=300,
)
```

### Безопасное чтение ключей

```python
import os
from ai_magic import Settings

settings = Settings(credentials=[{
    "provider": "gemini",
    "key": os.environ["GEMINI_API_KEY"],
    "models": ["gemini-3.7-flash"],
}])
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
