# Contributing

Стандарт разработки проекта «Вопрошалыч» — виртуальный помощник студента ТюмГУ.

> Если в репозитории вы обнаружите несоответствие с оглашёнными ниже положениями, создайте issue с указанием строки кода и несоответствия.

## Структура проекта

```
voproshalych/
├── bot-service/
│   ├── core/           # bot-core: связка с Dify (FastAPI)
│   └── bots/           # Адаптеры VK (Python) и MAX (Go)
├── docker-compose.yml
└── .env.example
```

## Стандарт оформления кода

### Python

- **PEP 8** — базовый стиль кода
- **Black** — автоформатирование, длина строки 88 символов
- **Google-style docstrings** — документирование функций и классов
- **isort** — сортировка импортов

Пример docstring:

```python
def process_question(question: str, context: str | None = None) -> dict:
    """Обработать вопрос пользователя.

    Args:
        question: Текст вопроса.
        context: История диалога.

    Returns:
        dict: Ответ с ключами answer, model, sources.
    """
```

### Go (max-bot)

- `gofmt` / `goimports`
- Стандартные рекомендации Effective Go

## Git Commits

Формат: Conventional Commits

```
type(scope): description

[optional body]
```

**Типы:**
- `feat` — новая функциональность
- `fix` — исправление бага
- `refactor` — рефакторинг без изменения поведения
- `docs` — документация
- `test` — тесты
- `chore` — сборка, CI, зависимости
- `perf` — оптимизация производительности

**Области (scope):**
- `bot` — bot-core
- `vk` — адаптер VK
- `max` — адаптер MAX

Примеры:
```
feat(bot): reset Dify conversation on new dialog
fix(vk): increase bot-core timeout
docs: update CONTRIBUTING.MD
```

## Тестирование

### Запуск тестов

```bash
cd bot-service/core
pytest tests -v
```

### Правила тестирования

- Юнит-тесты для бизнес-логики (сервисы, утилиты, парсеры)
- Интеграционные тесты для API endpoints (с моками внешних зависимостей)
- Тесты лежат в `bot-service/core/tests/`
- Имена файлов: `test_*.py`
- Фикстуры в `conftest.py`

## Запуск и разработка

```bash
# Скопировать env
cp .env.example .env

# Запустить все сервисы
docker compose up -d --build

# Только VK
docker compose up -d --build postgres bot-core vk-bot
```

## Code Review

- Каждый Merge Request проходит ревью
- Проверяются: стиль кода, тесты, документация
- MR не принимается с проваленными тестами или ошибками линтера
