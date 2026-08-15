# ai_magic

Асинхронный Python 3.12 клиент с OpenAI-подобным интерфейсом, ротацией ключей, моделей и провайдеров.

## Установка

```bash
pip install -e .
```

## Встроенные провайдеры

`groq`, `nvidia`, `openrouter`, `gemini`, `together`, `mistral`, `cohere`, `hyperbolic`.
Gemini и Cohere используют собственные adapters; остальные — OpenAI-compatible контракт.

## Конфигурация через AI_MAGIC_CREDENTIALS

Значение — JSON-массив credentials. Один provider:

```bash
AI_MAGIC_CREDENTIALS=[{"provider":"groq","key":"gsk_...","model":"llama-3.3-70b-versatile"}]
AI_MAGIC_CREDENTIALS=[{"provider":"nvidia","key":"nvapi-...","model":"meta/llama-3.1-70b-instruct"}]
AI_MAGIC_CREDENTIALS=[{"provider":"openrouter","key":"sk-or-...","model":"openai/gpt-4o-mini"}]
AI_MAGIC_CREDENTIALS=[{"provider":"gemini","key":"...","model":"gemini-2.0-flash"}]
AI_MAGIC_CREDENTIALS=[{"provider":"together","key":"...","model":"meta-llama/Llama-3.3-70B-Instruct-Turbo"}]
AI_MAGIC_CREDENTIALS=[{"provider":"mistral","key":"...","model":"mistral-small-latest"}]
AI_MAGIC_CREDENTIALS=[{"provider":"cohere","key":"...","model":"command-r-plus"}]
AI_MAGIC_CREDENTIALS=[{"provider":"hyperbolic","key":"...","model":"meta-llama/Meta-Llama-3.1-70B-Instruct"}]
```

Несколько ключей одного provider:

```bash
AI_MAGIC_CREDENTIALS=[{"provider":"groq","key":"key-1","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]},{"provider":"groq","key":"key-2","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]}]
```

`model` задаёт модель по умолчанию credential. `models` задаёт разрешённую цепочку моделей; первый элемент также служит default, если `model` отсутствует. Явный `model=` в запросе принудителен, но выбираются только credentials, в чьём `models`/`model` он разрешён.

Межпровайдерская ротация:

```bash
AI_MAGIC_CREDENTIALS=[{"provider":"groq","key":"...","models":["llama-3.3-70b-versatile","llama-3.1-8b-instant"]},{"provider":"cohere","key":"...","models":["command-r-plus"]},{"provider":"gemini","key":"...","models":["gemini-2.0-flash"]}]
```

Без явной модели каждый credential использует собственную default-модель. При 429/5xx/transport error карусель переходит к следующему credential. `Retry-After` определяет срок блокировки ключа. Если все совместимые ключи временно заблокированы, клиент ждёт ближайшую разблокировку не дольше `max_credential_wait` (по умолчанию 30 секунд).

### Совместимость моделей

Модели принадлежат provider: Groq-модель нельзя отправлять в Gemini или Cohere. Поэтому для безопасного fallback перечисляйте `models` у каждого credential. Credential без `model` и `models` считается универсальным — это удобно для OpenAI-compatible custom provider, но ответственность за совместимость лежит на вызывающем коде.

### OpenRouter headers

```bash
OPENROUTER_HTTP_REFERER=https://example.com
OPENROUTER_X_TITLE=My Application
```

Их также можно задать в `headers` конкретного credential.

## Использование

```python
import asyncio
from ai_magic import AsyncAIMagic

async def main() -> None:
    async with AsyncAIMagic() as client:
        response = await client.chat.completions.create(
            messages=[{"role": "user", "content": "Hello"}],
            model="llama-3.3-70b-versatile",  # необязательно
            session_id="user-42",
        )
        print(response.choices[0].message.content)

asyncio.run(main())
```

## High-level API: `chat()` и `code()`

Helpers возвращают строку из первого ответа модели и подходят для коротких async-сценариев. В `prompt` передаётся пользовательский запрос; дополнительные именованные аргументы (`model`, `temperature`, `max_tokens` и другие поля completion) без изменений передаются клиенту.

### Простой диалог

```python
import asyncio
from ai_magic import chat

async def main() -> None:
    answer = await chat("Объясни async/await в трёх предложениях")
    print(answer)

asyncio.run(main())
```

Без `client=` helper создаёт временный `AsyncAIMagic` и гарантированно закрывает его после запроса. Это удобно для одного вызова, но для нескольких запросов лучше переиспользовать клиент:

```python
import asyncio
from ai_magic import AsyncAIMagic, chat

async def main() -> None:
    async with AsyncAIMagic() as client:
        first = await chat("Представься", client=client)
        second = await chat("Назови один факт о Python", client=client)
        print(first, second)

asyncio.run(main())
```

Переданный пользователем клиент helper не закрывает: его жизненным циклом управляет вызывающий код. Используйте `async with` либо вызовите `await client.aclose()` в `finally`.

### Контекст сессии и system prompt

`session_id` принимает `str | int`. Одинаковый идентификатор при повторном использовании одного клиента сохраняет контекст разговора; целое число безопасно преобразуется в строку. `system` задаёт инструкции только для `chat()`:

```python
import asyncio
from ai_magic import AsyncAIMagic, chat

async def main() -> None:
    async with AsyncAIMagic() as client:
        await chat(
            "Меня зовут Михаил",
            client=client,
            session_id=42,
            system="Отвечай кратко и по-русски.",
        )
        answer = await chat("Как меня зовут?", client=client, session_id=42)
        print(answer)

asyncio.run(main())
```

История сессии хранится внутри конкретного экземпляра `AsyncAIMagic`, поэтому новый временный клиент не продолжит предыдущую сессию.

### Модель и параметры генерации

```python
answer = await chat(
    "Предложи имя функции",
    model="llama-3.3-70b-versatile",
    temperature=0.2,
    max_tokens=100,
)
```

Модель должна быть разрешена хотя бы для одного настроенного credential. Поддержка конкретных параметров зависит от provider и общего completion-контракта.

### Генерация чистого кода

`code()` требует от модели вернуть только исходный код — без Markdown fences и пояснений. Если модель всё же обернула весь ответ в один внешний блок ``````, helper снимает только эту внешнюю обёртку и не изменяет обычный код.

```python
import asyncio
from ai_magic import code

async def main() -> None:
    source = await code(
        "Напиши Python-функцию add(a, b) с type hints",
        temperature=0,
        max_tokens=200,
    )
    print(source)

asyncio.run(main())
```

Сгенерированный код может содержать ошибки или опасные операции. Проверяйте его и **не выполняйте недоверенный код** без ручного аудита и подходящей изоляции.

## Custom provider

OpenAI-compatible provider регистрируется без нового adapter:

```python
from ai_magic import AsyncAIMagic, Credential, KeyManager, ProviderRegistry

registry = ProviderRegistry()
registry.register_openai_compatible(
    "my-provider", "https://api.example.com/v1",
    headers={"X-Custom-Header": "value"},
)
credentials = KeyManager([
    Credential("my-provider", "secret", models=("model-a", "model-b")),
])
client = AsyncAIMagic(registry=registry, credentials=credentials)
```

Для нестандартного API создайте `ProviderAdapter` и зарегистрируйте `ProviderConfig`; подробности — в [DEVELOPERS.md](DEVELOPERS.md).

Не коммитьте ключи: храните их в переменных окружения или secret manager.

## Установка из Git

```powershell
python -m pip install "git+https://github.com/<owner>/<repository>.git@main"
```

Для разработки клонируйте репозиторий и установите пакет с тестовыми зависимостями:

```powershell
git clone https://github.com/<owner>/<repository>.git
Set-Location <repository>
python -m pip install -e ".[test]"
python -m pytest -q
```

Перед публикацией проверьте, что `.env`, ключи, виртуальные окружения, кэши и build artifacts не попали в индекс Git.
