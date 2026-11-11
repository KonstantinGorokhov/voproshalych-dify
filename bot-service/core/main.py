"""FastAPI-приложение, которое отдает общую бизнес-логику бота."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from db import init_db
from models.callback import CallbackEvent
from models.message import IncomingMessage
from models.response import BotResponse
from services.bot_service import BotService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot_service = BotService()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Создаёт таблицы при старте."""

    init_db()
    if not settings.dify_api_key:
        logger.warning("DIFY_API_KEY is not set: requests to Dify will fail")
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    """Возвращает статус доступности сервиса."""

    return {"status": "ok"}


@app.post("/messages", response_model=BotResponse)
def process_message(message: IncomingMessage) -> BotResponse:
    """Обрабатывает нормализованное сообщение от адаптера платформы.

    Эндпоинт синхронный: FastAPI выполняет его в пуле потоков, поэтому
    долгий blocking-запрос в Dify не блокирует остальные запросы.
    """

    logger.info(
        "Message from %s:%s type=%s",
        message.platform.value,
        message.user_id,
        message.message_type.value,
    )
    return bot_service.handle_message(message)


@app.post("/callbacks", response_model=BotResponse)
def process_callback(event: CallbackEvent) -> BotResponse:
    """Обрабатывает callback-событие от адаптера платформы."""

    return bot_service.handle_callback(event)
