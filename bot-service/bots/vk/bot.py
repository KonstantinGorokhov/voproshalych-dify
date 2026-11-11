"""VK-адаптер на базе vkbottle.

Модуль принимает события VK, нормализует входящие текстовые сообщения к общему
контракту core, отправляет их в FastAPI-сервис и выполняет возвращенные действия.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import re
from vkbottle import Callback, GroupEventType, GroupTypes, Keyboard, Text, OpenLink
from vkbottle.bot import Bot, Message


logging.basicConfig(level=logging.INFO)

_MSG_SERVICE_UNAVAILABLE = "Сервис временно недоступен. Попробуйте позже."
_MSG_PENDING = "Сейчас я попробую ответить на этот вопрос, это может занять какое-то время..."


logging.basicConfig(level=logging.INFO)


@dataclass(slots=True)
class Settings:
    """Настройки запуска VK-адаптера."""

    vk_bot_token: str = os.getenv("VK_BOT_TOKEN", "")
    bot_core_url: str = os.getenv("BOT_CORE_URL", "http://127.0.0.1:8000")
    request_timeout_seconds: float = float(
        os.getenv("BOT_CORE_TIMEOUT_SECONDS", "300")
    )


class CoreClient:
    """Асинхронный HTTP-клиент для общего bot core."""

    def __init__(self, settings: Settings) -> None:
        """Инициализирует HTTP-клиент.

        Args:
            settings: Настройки запуска VK-адаптера.
        """

        self._client = httpx.AsyncClient(
            base_url=settings.bot_core_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
        )

    async def close(self) -> None:
        """Закрывает внутренний HTTP-клиент."""

        await self._client.aclose()

    async def process_message(self, message: Message) -> dict[str, Any]:
        """Отправляет нормализованное сообщение VK в core-сервис.

        Args:
            message: Сообщение VK, полученное через vkbottle.

        Returns:
            dict[str, Any]: JSON-ответ от core-сервиса.
        """

        payload = self._build_payload(message)
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    async def process_callback(self, event: GroupTypes.MessageEvent) -> dict[str, Any]:
        """Отправляет callback VK в core-сервис.

        Args:
            event: Callback-событие VK.

        Returns:
            dict[str, Any]: JSON-ответ от core-сервиса.
        """

        payload = build_callback_payload(event)
        response = await self._client.post("/callbacks", json=payload)
        response.raise_for_status()
        return response.json()

    def _build_payload(self, message: Message) -> dict[str, Any]:
        """Преобразует сообщение VK в общий контракт core.

        Args:
            message: Сообщение VK, полученное через vkbottle.

        Returns:
            dict[str, Any]: JSON-полезная нагрузка по входной схеме core.
        """

        sent_at = datetime.now(UTC).isoformat()
        if message.date is not None:
            sent_at = datetime.fromtimestamp(message.date, tz=UTC).isoformat()

        return {
            "platform": "vk",
            "message_type": detect_message_type(message),
            "user_id": str(message.from_id),
            "chat_id": str(message.peer_id),
            "text": message.text or "",
            "message_id": str(message.conversation_message_id)
            if message.conversation_message_id is not None
            else None,
            "timestamp": sent_at,
            "metadata": {
                "peer_id": message.peer_id,
                "conversation_message_id": message.conversation_message_id,
                "group_id": message.group_id,
            },
        }


def build_bot(settings: Settings, core_client: CoreClient) -> Bot:
    """Создает экземпляр VK-бота и подключает обработчики сообщений.

    Args:
        settings: Настройки запуска VK-адаптера.
        core_client: HTTP-клиент для общего core-сервиса.

    Returns:
        Bot: Настроенный VK-бот.
    """

    bot = Bot(settings.vk_bot_token)

    @bot.on.message()
    async def handle_start_or_message(message: Message) -> None:
        """Обрабатывает /start, 'Начать' или обычное сообщение.

        Args:
            message: Сообщение VK.
        """
        text = (message.text or "").strip().lower()
        is_start = text in {"/start", "start", "начать"}
        is_quick_button = text in {"помощь", "новый диалог", "📋 помощь", "🔄 новый диалог"}

        pending_message_id: int | None = None
        if should_show_pending_message(message) and not is_start and not is_quick_button:
            pending_message_id = await send_pending_message(bot, message)

        try:
            bot_response = await core_client.process_message(message)
        except httpx.HTTPError:
            logging.exception("Не удалось обработать сообщение VK через core")
            await delete_pending_message(bot, pending_message_id)
            await message.answer(_MSG_SERVICE_UNAVAILABLE)
            return

        await delete_pending_message(bot, pending_message_id)

        for action in bot_response.get("actions", []):
            if action.get("type") == "send_text" and action.get("text"):
                text = action["text"]
                if action.get("parse_mode") == "HTML":
                    text = _strip_html_to_plain(text)
                keyboard = build_inline_keyboard(action.get("buttons", []))
                reply_keyboard = build_reply_keyboard(
                    action.get("reply_keyboard", [])
                )
                try:
                    await message.answer(
                        text,
                        keyboard=reply_keyboard or keyboard,
                    )
                except Exception:
                    logging.exception("Failed to send VK message")

    @bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=GroupTypes.MessageEvent)
    async def handle_callback(event: GroupTypes.MessageEvent) -> None:
        """Проксирует callback VK в core и подтверждает результат.

        Args:
            event: Callback-событие VK.
        """

        event_id = getattr(event, "event_id", None)
        user_id = extract_event_field(event, "user_id")
        peer_id = extract_event_field(event, "peer_id")

        if event_id is None or user_id is None or peer_id is None:
            logging.error("VK callback event is missing required identifiers")
            return

        # Сразу снимаем спиннер с кнопки, до HTTP-запроса к core
        await _dismiss_button_loading(bot, event_id, user_id, peer_id)

        bot_response: dict[str, Any] | None = None

        try:
            bot_response = await core_client.process_callback(event)
        except (httpx.HTTPStatusError, httpx.HTTPError):
            logging.exception("HTTP error from core VK callback")

        if not bot_response:
            return

        for action in bot_response.get("actions", []):
            keyboard = build_inline_keyboard(action.get("buttons", []))

            if action.get("text"):
                try:
                    await bot.api.messages.send(
                        peer_id=peer_id,
                        random_id=random.randint(1, 2_147_483_647),
                        message=action["text"],
                        keyboard=keyboard,
                    )
                except Exception:
                    logging.exception("Не удалось отправить callback-ответ VK пользователю")

    return bot


async def _dismiss_button_loading(
    bot: Bot, event_id: str, user_id: int, peer_id: int,
) -> None:
    """Снять спиннер с inline-кнопки VK без показа уведомления.

    VK API: если event_data не передан, кнопка просто выходит из
    состояния загрузки без показа snackbar.
    """
    try:
        await bot.api.messages.send_message_event_answer(
            event_id=event_id,
            user_id=user_id,
            peer_id=peer_id,
        )
    except Exception:
        logging.exception("Не удалось снять спиннер с кнопки VK")


def _strip_html_to_plain(text: str) -> str:
    """Преобразует HTML-разметку в обычный текст для VK.

    VK не поддерживает HTML в сообщениях, поэтому
    <a href="url">текст</a> → текст\n— url
    """
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2\n— \1", text)
    return text


VK_INLINE_EXCLUDED: set[str] = set()


def build_inline_keyboard(button_rows: list[list[dict[str, str]]]) -> str | None:
    """Преобразует кнопки из ответа core в VK inline keyboard.

    Поддерживает callback-кнопки и URL-кнопки (OpenLink).

    Args:
        button_rows: Строки кнопок из ответа core.

    Returns:
        str | None: JSON-представление клавиатуры или `None`.
    """

    if not button_rows:
        return None

    filtered_rows: list[list[dict[str, str]]] = []
    for row in button_rows:
        filtered = [b for b in row if b.get("callback_data") not in VK_INLINE_EXCLUDED]
        if filtered:
            filtered_rows.append(filtered)

    if not filtered_rows:
        return None

    keyboard = Keyboard(inline=True)
    for row_index, row in enumerate(filtered_rows):
        if row_index > 0:
            keyboard.row()

        for button in row:
            if button.get("url"):
                keyboard.add(
                    OpenLink(link=button["url"], label=button["text"])
                )
            else:
                keyboard.add(
                    Callback(button["text"], payload={"command": button.get("callback_data", "")})
                )

    return keyboard.get_json()


def build_reply_keyboard(button_rows: list[list[dict[str, str]]]) -> str | None:
    """Преобразует кнопки из ответа core в VK reply keyboard.

    Args:
        button_rows: Строки кнопок из ответа core.

    Returns:
        str | None: JSON-представление клавиатуры или `None`.
    """

    if not button_rows:
        return None

    keyboard = Keyboard(one_time=False)
    for row_index, row in enumerate(button_rows):
        if row_index > 0:
            keyboard.row()
        for button in row:
            keyboard.add(Text(button["text"]))

    return keyboard.get_json()


def extract_event_field(event: GroupTypes.MessageEvent, field_name: str) -> Any:
    """Безопасно извлекает поле из VK callback event.

    Args:
        event: Callback-событие VK.
        field_name: Имя поля.

    Returns:
        Any: Значение поля или `None`.
    """

    if hasattr(event, field_name):
        return getattr(event, field_name)

    event_object = getattr(event, "object", None)
    if event_object is not None and hasattr(event_object, field_name):
        return getattr(event_object, field_name)

    return None


def build_callback_payload(event: GroupTypes.MessageEvent) -> dict[str, Any]:
    """Преобразует callback VK в общий контракт core.

    Args:
        event: Callback-событие VK.

    Returns:
        dict[str, Any]: JSON-полезная нагрузка для `POST /callbacks`.
    """

    raw_payload = extract_event_field(event, "payload") or {}
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            raw_payload = {"command": raw_payload}

    callback_data = ""
    if isinstance(raw_payload, dict):
        callback_data = str(raw_payload.get("command", ""))

    conversation_message_id = extract_event_field(event, "conversation_message_id")
    peer_id = extract_event_field(event, "peer_id")
    user_id = extract_event_field(event, "user_id")

    return {
        "platform": "vk",
        "user_id": str(user_id),
        "chat_id": str(peer_id),
        "callback_data": callback_data,
        "message_id": str(conversation_message_id)
        if conversation_message_id is not None
        else None,
        "metadata": {
            "peer_id": peer_id,
            "event_id": extract_event_field(event, "event_id"),
        },
    }


def detect_message_type(message: Message) -> str:
    """Определяет платформенно-независимый тип сообщения VK.

    Args:
        message: Сообщение VK.

    Returns:
        str: Тип сообщения для контракта core.
    """

    if message.attachments:
        attachment_type = getattr(message.attachments[0], "type", None)
        if attachment_type in {
            "sticker",
            "photo",
            "video",
            "audio",
            "doc",
            "audio_message",
        }:
            if attachment_type == "audio_message":
                return "voice"
            if attachment_type == "doc":
                return "document"
            return attachment_type

    if message.text:
        return "text"
    return "unknown"


def should_show_pending_message(message: Message) -> bool:
    """Определяет, нужно ли показывать временное сообщение ожидания.

    Args:
        message: Сообщение VK.

    Returns:
        bool: `True`, если нужно показать сообщение ожидания.
    """

    if detect_message_type(message) != "text":
        return False

    normalized_text = (message.text or "").strip().lower()
    return normalized_text not in {"/start", "start", "начать"}


async def send_pending_message(bot: Bot, message: Message) -> int | None:
    """Отправляет временное сообщение ожидания.

    Args:
        bot: Экземпляр VK-бота.
        message: Входящее сообщение.

    Returns:
        int | None: Идентификатор отправленного сообщения.
    """

    try:
        return await bot.api.messages.send(
            peer_id=message.peer_id,
            random_id=random.randint(1, 2_147_483_647),
            message=_MSG_PENDING,
        )
    except Exception:
        logging.exception("Не удалось отправить временное сообщение VK")
        return None


async def delete_pending_message(bot: Bot, message_id: int | None) -> None:
    """Удаляет временное сообщение ожидания.

    Args:
        bot: Экземпляр VK-бота.
        message_id: Идентификатор сообщения.
    """

    if message_id is None:
        return

    try:
        await bot.api.messages.delete(
            message_ids=[message_id],
            delete_for_all=1,
        )
    except Exception:
        logging.exception("Не удалось удалить временное сообщение VK")


def main() -> None:
    """Запускает long polling VK и пересылает сообщения в core-сервис."""

    settings = Settings()
    if not settings.vk_bot_token:
        raise RuntimeError("VK_BOT_TOKEN is not set")

    core_client = CoreClient(settings)
    bot = build_bot(settings, core_client)

    try:
        bot.run_forever()
    finally:
        asyncio.run(core_client.close())


if __name__ == "__main__":
    main()
