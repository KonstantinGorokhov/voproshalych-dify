# Виртуальный помощник студента ТюмГУ, чат-бот «Вопрошалыч»

Чат-бот для студентов ТюмГУ в VK и MAX. Репозиторий — **витрина**: он принимает
сообщения из мессенджеров и пересылает их приложению Chatflow на платформе
[Dify](https://dify.ai). Логика формирования ответа (база знаний, поиск, LLM,
память диалога, аналитика) настраивается в Dify.

Заказчик: ФГАОУ ВО «Тюменский государственный университет», управление по сопровождению студентов «Единый деканат».

Исполнители: Ефимова Мария Александровна, Горохов Константин Алексеевич, Батыев Рамиль Рустамович, Мустаков Максим Рашитович.

## Архитектура

```
VK ──► vk-bot  ─┐
                ├─► bot-core ──► Dify (Chatflow, Service API)
MAX ─► max-bot ─┘       │
                        ▼
                    postgres (пользователи, conversation_id, оценки)
```

| Компонент | Описание |
|-----------|----------|
| **vk-bot** | Адаптер VK (Python, vkbottle) |
| **max-bot** | Адаптер MAX (Go) |
| **bot-core** | FastAPI: меню, связка пользователь ↔ диалог Dify, «Новый диалог», оценки |
| **postgres** | PostgreSQL 18: таблицы `users`, `dify_messages` |

Подробнее — [bot-service/docs/overview.md](bot-service/docs/overview.md).

## Быстрый запуск

```bash
cp .env.example .env
# заполнить VK_BOT_TOKEN, MAX_BOT_TOKEN, DIFY_API_URL, DIFY_API_KEY
docker compose up -d --build
docker compose ps
```

Если Dify запущен на том же сервере в своём docker compose, укажите
`DIFY_API_URL=http://host.docker.internal/v1`.

Проверка без мессенджеров:

```bash
curl -s localhost:8000/messages -H 'Content-Type: application/json' \
  -d '{"platform":"vk","user_id":"1","chat_id":"1","text":"Как получить справку об обучении?"}'
```

## Тесты

```bash
cd bot-service/core
pip install -e "..[core]" pytest
pytest tests -v
```
