"""Общие входные модели для платформенных адаптеров и core-сервиса."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """Поддерживаемые платформы ботов."""

    vk = "vk"
    max = "max"


class MessageType(str, Enum):
    """Поддерживаемые типы входящих сообщений."""

    text = "text"
    voice = "voice"
    sticker = "sticker"
    photo = "photo"
    video = "video"
    audio = "audio"
    document = "document"
    unknown = "unknown"


class IncomingMessage(BaseModel):
    """Нормализованное сообщение, полученное от любого адаптера платформы.

    Attributes:
        platform: Идентификатор исходной платформы.
        message_type: Тип входящего сообщения.
        user_id: Идентификатор пользователя на конкретной платформе.
        chat_id: Идентификатор чата на конкретной платформе.
        text: Нормализованный текст сообщения, если он есть.
        message_id: Идентификатор сообщения на конкретной платформе.
        timestamp: Исходное время сообщения, если оно доступно.
        metadata: Дополнительные платформенно-специфичные поля.
    """

    platform: Platform
    message_type: MessageType = Field(
        default=MessageType.text,
        description="Тип входящего сообщения",
    )
    user_id: str = Field(..., description="Идентификатор пользователя на платформе")
    chat_id: str = Field(..., description="Идентификатор чата на платформе")
    text: str | None = Field(
        default=None,
        description="Нормализованный текст сообщения, если он присутствует",
    )
    message_id: str | None = Field(
        default=None,
        description="Идентификатор сообщения на платформе",
    )
    timestamp: datetime | None = Field(
        default=None,
        description="Время создания сообщения, если платформа его передает",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Дополнительные поля платформы вне основного контракта core",
    )
