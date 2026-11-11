"""Тесты логики диалога: conversation_id, новый диалог, оценки."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from messages import DIALOG_RESET, FEEDBACK_LIKE, SERVICE_UNAVAILABLE
from models.callback import CallbackEvent
from models.message import IncomingMessage
from services.bot_service import BotService
from services.dify_client import DifyAnswer, DifyConversationNotFound, DifyTimeout
from services.feedback_service import FeedbackResult


USER = SimpleNamespace(id=7, dify_user="vk:42")


def make_service():
    dify = MagicMock()
    service = BotService(dify_client=dify)
    service._user_service = MagicMock()
    service._user_service.upsert_user.return_value = USER
    service._user_service.get_user.return_value = USER
    service._feedback_service = MagicMock()
    service._feedback_service.save_answer.return_value = 100
    return service, dify


def message(text: str) -> IncomingMessage:
    return IncomingMessage(platform="vk", user_id="42", chat_id="42", text=text)


def callback(data: str) -> CallbackEvent:
    return CallbackEvent(platform="vk", user_id="42", chat_id="42", callback_data=data)


def test_question_continues_saved_conversation() -> None:
    service, dify = make_service()
    service._user_service.get_conversation_id.return_value = "conv-1"
    dify.chat.return_value = DifyAnswer("**Ответ**", "conv-1", "msg-1")

    response = service.handle_message(message("Когда сессия?"))

    dify.chat.assert_called_once_with(query="Когда сессия?", user="vk:42", conversation_id="conv-1")
    service._user_service.set_conversation_id.assert_not_called()
    action = response.actions[0]
    assert action.text == "Ответ"
    assert [b.callback_data for b in action.buttons[-1]] == ["feedback:like:100", "feedback:dislike:100"]


def test_first_question_stores_new_conversation_id() -> None:
    service, dify = make_service()
    service._user_service.get_conversation_id.return_value = None
    dify.chat.return_value = DifyAnswer("Ответ", "conv-new", "msg-1")

    service.handle_message(message("Где деканат?"))

    service._user_service.set_conversation_id.assert_called_once_with(7, "conv-new")


def test_lost_conversation_restarts_dialog() -> None:
    service, dify = make_service()
    service._user_service.get_conversation_id.return_value = "gone"
    dify.chat.side_effect = [DifyConversationNotFound("x"), DifyAnswer("Ответ", "conv-2", "msg-2")]

    response = service.handle_message(message("Вопрос"))

    assert dify.chat.call_args_list[1].kwargs["conversation_id"] is None
    service._user_service.set_conversation_id.assert_called_once_with(7, "conv-2")
    assert response.actions[0].text == "Ответ"


def test_dify_error_returns_service_unavailable() -> None:
    service, dify = make_service()
    service._user_service.get_conversation_id.return_value = None
    dify.chat.side_effect = DifyTimeout("t")

    response = service.handle_message(message("Вопрос"))

    assert response.actions[0].text == SERVICE_UNAVAILABLE
    service._feedback_service.save_answer.assert_not_called()


def test_new_dialog_button_and_text_reset_conversation() -> None:
    service, _ = make_service()
    service._user_service.set_conversation_id.return_value = True

    assert service.handle_callback(callback("dialog:start_new")).actions[0].text == DIALOG_RESET
    assert service.handle_message(message("🔄 Новый диалог")).actions[0].text == DIALOG_RESET
    assert service._user_service.set_conversation_id.call_count == 2
    service._user_service.set_conversation_id.assert_called_with(7, None)


def test_feedback_is_saved_and_sent_to_dify() -> None:
    service, dify = make_service()
    service._feedback_service.save_feedback.return_value = FeedbackResult("saved", "msg-1")

    response = service.handle_callback(callback("feedback:like:100"))

    service._feedback_service.save_feedback.assert_called_once_with(7, "like", 100)
    dify.send_feedback.assert_called_once_with("msg-1", "like", "vk:42")
    assert response.actions[0].text == FEEDBACK_LIKE


def test_legacy_feedback_without_id_rates_last_answer() -> None:
    service, _ = make_service()
    service._feedback_service.save_feedback.return_value = FeedbackResult("saved", "msg-1")

    service.handle_callback(callback("feedback:dislike"))

    service._feedback_service.save_feedback.assert_called_once_with(7, "dislike", None)


def test_start_menu_has_no_subscription_button() -> None:
    service, dify = make_service()

    response = service.handle_message(message("/start"))

    data = [b.callback_data for row in response.actions[0].buttons for b in row]
    assert data == ["menu:help", "dialog:start_new"]
    dify.chat.assert_not_called()
