"""Подключение к базе данных и ORM-модели bot-core.

Логика ответов живёт в Dify, поэтому здесь хранится только то, что нужно
витрине: пользователь мессенджера, его текущий диалог в Dify и ответы,
к которым привязаны оценки.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import settings


DATABASE_URL = (
    f"postgresql+psycopg2://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
)


engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей bot-core."""


class User(Base):
    """Пользователь мессенджера и его текущий диалог в Dify.

    Attributes:
        platform: Платформа пользователя (`vk`, `max`).
        platform_user_id: Идентификатор пользователя на платформе.
        dify_conversation_id: Текущий диалог в Dify; `None` — следующий
            вопрос начнёт новый диалог.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "platform_user_id",
            name="uq_users_platform_platform_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dify_conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    @property
    def dify_user(self) -> str:
        """Стабильный идентификатор пользователя для параметра `user` в Dify."""

        return f"{self.platform}:{self.platform_user_id}"


class DifyMessage(Base):
    """Ответ Dify, показанный пользователю; к нему привязывается оценка."""

    __tablename__ = "dify_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dify_conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dify_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    feedback: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""

    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Создает новую SQLAlchemy-сессию."""

    return SessionLocal()
