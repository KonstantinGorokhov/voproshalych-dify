"""Сервис для работы с пользователями бота и их диалогами в Dify."""

from __future__ import annotations

from sqlalchemy.sql import func

from db import User, get_session
from models.message import IncomingMessage


def _user_identity_from_metadata(metadata: dict) -> tuple[str | None, str | None, str | None]:
    """Нормализует отображаемые поля пользователя из metadata разных платформ."""

    username = metadata.get("username") or metadata.get("sender_name")
    first_name = metadata.get("first_name")
    last_name = metadata.get("last_name")
    return username, first_name, last_name


class UserService:
    """Создает и обновляет пользователей в базе данных."""

    def upsert_user(self, message: IncomingMessage) -> User | None:
        """Создает или обновляет пользователя по входящему сообщению.

        Если два первых сообщения пришли одновременно, второй INSERT упадёт
        на уникальном ключе — тогда просто перечитываем созданную запись.

        Returns:
            User | None: Сохраненный пользователь или `None` при ошибке.
        """

        user = self._upsert_user_once(message)
        if user is None:
            user = self._upsert_user_once(message)
        return user

    def _upsert_user_once(self, message: IncomingMessage) -> User | None:
        """Одна попытка upsert пользователя."""

        session = get_session()
        try:
            user = (
                session.query(User)
                .filter(
                    User.platform == message.platform.value,
                    User.platform_user_id == message.user_id,
                )
                .one_or_none()
            )

            username, first_name, last_name = _user_identity_from_metadata(
                message.metadata or {}
            )
            if user is None:
                user = User(
                    platform=message.platform.value,
                    platform_user_id=message.user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                )
                session.add(user)
            else:
                user.username = username or user.username
                user.first_name = first_name or user.first_name
                user.last_name = last_name or user.last_name
                user.updated_at = func.now()

            session.commit()
            session.refresh(user)
            return user
        except Exception:
            session.rollback()
            return None
        finally:
            session.close()

    def get_user(self, platform: str, platform_user_id: str) -> User | None:
        """Возвращает пользователя по платформе и идентификатору."""

        session = get_session()
        try:
            return (
                session.query(User)
                .filter(
                    User.platform == platform,
                    User.platform_user_id == platform_user_id,
                )
                .one_or_none()
            )
        finally:
            session.close()

    def get_conversation_id(self, user_id: int) -> str | None:
        """Возвращает текущий conversation_id пользователя в Dify."""

        session = get_session()
        try:
            user = session.get(User, user_id)
            return user.dify_conversation_id if user is not None else None
        finally:
            session.close()

    def set_conversation_id(self, user_id: int, conversation_id: str | None) -> bool:
        """Сохраняет conversation_id (`None` — следующий вопрос начнёт новый диалог).

        Returns:
            bool: `True`, если пользователь найден и обновлён.
        """

        session = get_session()
        try:
            updated = (
                session.query(User)
                .filter(User.id == user_id)
                .update(
                    {"dify_conversation_id": conversation_id, "updated_at": func.now()},
                    synchronize_session=False,
                )
            )
            session.commit()
            return bool(updated)
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()
