"""Точка входа в бизнес-логику для обработки нормализованных сообщений.

Ответы формирует Chatflow-приложение Dify. bot-core хранит связку
«пользователь мессенджера → conversation_id в Dify», управляет новым
диалогом и пересылает оценки ответов.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict

from config import settings
from messages import (
    ANSWER_FAILED,
    DIALOG_RESET,
    FEEDBACK_ALREADY_RATED,
    FEEDBACK_DISLIKE,
    FEEDBACK_LIKE,
    GREETING,
    HELP_CONTACTS,
    SERVICE_UNAVAILABLE,
    UNKNOWN_COMMAND,
    UNSUPPORTED_FORMAT,
    VOICE_STUB,
)
from models.callback import CallbackEvent
from models.message import IncomingMessage
from models.response import ActionType, BotResponse, InlineButton, OutgoingAction
from services.dify_client import (
    DifyAnswer,
    DifyClient,
    DifyConversationNotFound,
    DifyError,
)
from services.feedback_service import (
    FEEDBACK_DISLIKE as FEEDBACK_DISLIKE_TYPE,
    FEEDBACK_LIKE as FEEDBACK_LIKE_TYPE,
    FeedbackService,
)
from services.user_service import UserService

logger = logging.getLogger(__name__)

MAX_ANSWER_LENGTH = 3900
NEW_DIALOG_TEXTS = {"🔄 новый диалог", "новый диалог", "/new"}
HELP_TEXTS = {"/help", "📋 помощь", "помощь"}
START_TEXTS = {"/start", "start", "начать"}


class _UserLocks:
    """Блокировки на пользователя: вопросы одного пользователя идут по очереди.

    Без этого два быстрых вопроса подряд в новом диалоге создадут в Dify два
    разных диалога, потому что conversation_id известен только после ответа.
    Работает в пределах одного процесса uvicorn.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[int, threading.Lock] = defaultdict(threading.Lock)

    def get(self, user_id: int) -> threading.Lock:
        with self._guard:
            return self._locks[user_id]


def _text(text: str, buttons: list[list[InlineButton]] | None = None) -> BotResponse:
    """Ответ из одного текстового сообщения."""

    return BotResponse(
        actions=[
            OutgoingAction(type=ActionType.send_text, text=text, buttons=buttons or [])
        ]
    )


class BotService:
    """Обрабатывает нормализованные сообщения и возвращает платформенно-независимые действия."""

    SOURCE_URL_BUTTONS: list[list[InlineButton]] = [
        [InlineButton(text="🌐 Официальный сайт ТюмГУ", url="https://utmn.ru")],
        [InlineButton(text="📄 Сведения об организации", url="https://sveden.utmn.ru")],
        [InlineButton(text="📖 Инструкции для ИС", url="https://confluence.utmn.ru/pages/viewpage.action?pageId=3607500")],
        [InlineButton(text="📚 Руководства для обучающихся", url="https://confluence.utmn.ru/pages/viewpage.action?pageId=86478972")],
    ]

    def __init__(self, dify_client: DifyClient | None = None) -> None:
        self._dify = dify_client or DifyClient(
            base_url=settings.dify_api_url,
            api_key=settings.dify_api_key,
            timeout_seconds=settings.dify_timeout_seconds,
            source_url_field=settings.dify_source_url_field,
        )
        self._user_service = UserService()
        self._feedback_service = FeedbackService()
        self._locks = _UserLocks()

    # ------------------------------------------------------------------ входящие

    def handle_message(self, message: IncomingMessage) -> BotResponse:
        """Обрабатывает сообщение и возвращает действия для адаптера платформы."""

        user = self._user_service.upsert_user(message)

        if message.message_type == "text":
            return self._handle_text_message(message, user)
        if message.message_type == "voice":
            return _text(VOICE_STUB)
        return _text(UNSUPPORTED_FORMAT)

    def handle_callback(self, event: CallbackEvent) -> BotResponse:
        """Обрабатывает нажатие inline-кнопки."""

        data = event.callback_data

        if data == "menu:help":
            return self._build_help_response()

        if data in ("dialog:start_new", "menu:new_dialog"):
            user = self._user_service.get_user(event.platform.value, event.user_id)
            if user is None or not self._user_service.set_conversation_id(user.id, None):
                return _text(SERVICE_UNAVAILABLE)
            return _text(DIALOG_RESET)

        if data.startswith("feedback:"):
            return self._handle_feedback(event)

        return BotResponse(actions=[])

    # ------------------------------------------------------------------ текст

    def _handle_text_message(self, message: IncomingMessage, user) -> BotResponse:
        """Обрабатывает текстовое сообщение: команды меню или вопрос в Dify."""

        normalized_text = (message.text or "").strip()
        lowered_text = normalized_text.lower()

        if lowered_text in START_TEXTS:
            return _text(GREETING, self._build_start_buttons())
        if lowered_text in HELP_TEXTS:
            return self._build_help_response()
        if lowered_text in NEW_DIALOG_TEXTS:
            if user is not None:
                self._user_service.set_conversation_id(user.id, None)
            return _text(DIALOG_RESET)
        if lowered_text.startswith("/"):
            return _text(UNKNOWN_COMMAND)
        if not normalized_text:
            return _text(UNSUPPORTED_FORMAT)

        return self._answer_question(normalized_text, user)

    def _answer_question(self, question: str, user) -> BotResponse:
        """Задаёт вопрос в Dify в рамках текущего диалога пользователя."""

        if user is None:
            # БД недоступна: без сохранения диалога ответить можно, но без контекста.
            logger.warning("Answering without user record")
            try:
                result = self._dify.chat(query=question, user="anonymous")
            except DifyError:
                return _text(SERVICE_UNAVAILABLE)
            return _text(*self._format_answer(result, answer_id=None))

        with self._locks.get(user.id):
            # Читаем conversation_id внутри блокировки: предыдущий вопрос мог его обновить.
            conversation_id = self._user_service.get_conversation_id(user.id)
            try:
                result = self._ask(question, user.dify_user, conversation_id)
            except DifyError as exc:
                logger.error("Dify error: %s: %s", type(exc).__name__, exc)
                return _text(SERVICE_UNAVAILABLE)
            except Exception:
                logger.exception("Unexpected Dify error")
                return _text(ANSWER_FAILED)

            if result.conversation_id and result.conversation_id != conversation_id:
                self._user_service.set_conversation_id(user.id, result.conversation_id)

            answer_id = None
            if result.message_id:
                answer_id = self._feedback_service.save_answer(
                    user_id=user.id,
                    conversation_id=result.conversation_id,
                    dify_message_id=result.message_id,
                    question=question,
                    answer=result.answer,
                )

        return _text(*self._format_answer(result, answer_id=answer_id))

    def _ask(self, question: str, dify_user: str, conversation_id: str | None) -> DifyAnswer:
        """Запрос в Dify; если сохранённый диалог пропал — начинает новый."""

        try:
            return self._dify.chat(
                query=question, user=dify_user, conversation_id=conversation_id
            )
        except DifyConversationNotFound:
            logger.info("Conversation %s not found in Dify, starting new", conversation_id)
            return self._dify.chat(query=question, user=dify_user, conversation_id=None)

    def _format_answer(
        self, result: DifyAnswer, answer_id: int | None
    ) -> tuple[str, list[list[InlineButton]]]:
        """Готовит текст ответа и кнопки (источники + оценка)."""

        answer = self._strip_markdown(result.answer) or ANSWER_FAILED
        if len(answer) > MAX_ANSWER_LENGTH:
            answer = answer[: MAX_ANSWER_LENGTH - 50] + "\n\n..."

        buttons = [[InlineButton(text=s.label, url=s.url)] for s in result.sources[:3]]
        if answer_id is not None:
            buttons.append(
                [
                    InlineButton(text="❤️", callback_data=f"feedback:like:{answer_id}"),
                    InlineButton(text="👎", callback_data=f"feedback:dislike:{answer_id}"),
                ]
            )
        return answer, buttons

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Убирает markdown: VK и MAX показывают ответ обычным текстом."""

        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"__(.+?)__", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
        text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ------------------------------------------------------------------ оценки

    def _handle_feedback(self, event: CallbackEvent) -> BotResponse:
        """Обрабатывает `feedback:<like|dislike>[:<answer_id>]`."""

        parts = event.callback_data.split(":")
        rating = parts[1] if len(parts) > 1 else ""
        if rating not in (FEEDBACK_LIKE_TYPE, FEEDBACK_DISLIKE_TYPE):
            return BotResponse(actions=[])
        answer_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

        user = self._user_service.get_user(event.platform.value, event.user_id)
        if user is None:
            return _text(SERVICE_UNAVAILABLE)

        result = self._feedback_service.save_feedback(user.id, rating, answer_id)
        if result.status == "already_rated":
            return _text(FEEDBACK_ALREADY_RATED)
        if result.status != "saved":
            return _text(SERVICE_UNAVAILABLE)

        if result.dify_message_id:
            try:
                self._dify.send_feedback(result.dify_message_id, rating, user.dify_user)
            except DifyError as exc:
                # Оценка уже сохранена у нас; пользователю ошибку не показываем.
                logger.warning("Failed to send feedback to Dify: %s", exc)

        return _text(FEEDBACK_LIKE if rating == FEEDBACK_LIKE_TYPE else FEEDBACK_DISLIKE)

    # ------------------------------------------------------------------ меню

    def _build_help_response(self) -> BotResponse:
        """Справочное сообщение с контактами."""

        return _text(HELP_CONTACTS, self.SOURCE_URL_BUTTONS)

    @staticmethod
    def _build_start_buttons() -> list[list[InlineButton]]:
        """Кнопки стартового сообщения."""

        return [
            [InlineButton(text="📋 Помощь", callback_data="menu:help")],
            [InlineButton(text="🔄 Новый диалог", callback_data="dialog:start_new")],
        ]
