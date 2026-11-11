"""Сервис хранения ответов Dify и оценок к ним."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from db import DifyMessage, get_session

logger = logging.getLogger(__name__)

FEEDBACK_LIKE = "like"
FEEDBACK_DISLIKE = "dislike"


@dataclass(slots=True)
class FeedbackResult:
    """Результат сохранения оценки.

    Attributes:
        status: `saved`, `already_rated` или `error`.
        dify_message_id: Id сообщения в Dify, если оценку нужно отправить туда.
    """

    status: str
    dify_message_id: str | None = None


class FeedbackService:
    """Сохраняет показанные ответы и оценки пользователей."""

    def save_answer(
        self,
        user_id: int,
        conversation_id: str,
        dify_message_id: str,
        question: str,
        answer: str,
    ) -> int | None:
        """Сохраняет ответ Dify и возвращает его локальный id (для кнопок оценки)."""

        session = get_session()
        try:
            record = DifyMessage(
                user_id=user_id,
                dify_conversation_id=conversation_id,
                dify_message_id=dify_message_id,
                question=question,
                answer=answer,
            )
            session.add(record)
            session.commit()
            return record.id
        except Exception:
            session.rollback()
            logger.exception("Failed to save Dify answer")
            return None
        finally:
            session.close()

    def save_feedback(
        self,
        user_id: int,
        feedback_type: str,
        answer_id: int | None = None,
    ) -> FeedbackResult:
        """Сохраняет оценку ответа.

        Args:
            user_id: Локальный id пользователя.
            feedback_type: `like` или `dislike`.
            answer_id: Локальный id ответа из кнопки; `None` — последний ответ
                пользователя (для кнопок, отправленных до обновления).
        """

        session = get_session()
        try:
            query = session.query(DifyMessage).filter(DifyMessage.user_id == user_id)
            if answer_id is not None:
                query = query.filter(DifyMessage.id == answer_id)
            record = query.order_by(DifyMessage.id.desc()).first()
            if record is None:
                return FeedbackResult(status="error")
            if record.feedback == feedback_type:
                return FeedbackResult(status="already_rated")

            record.feedback = feedback_type
            dify_message_id = record.dify_message_id
            session.commit()
            return FeedbackResult(status="saved", dify_message_id=dify_message_id)
        except Exception:
            session.rollback()
            logger.exception("Failed to save feedback")
            return FeedbackResult(status="error")
        finally:
            session.close()
