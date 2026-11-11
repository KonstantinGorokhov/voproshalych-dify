"""Тесты клиента Dify на подменённом HTTP-транспорте."""

import json

import httpx
import pytest

from services.dify_client import (
    DifyClient,
    DifyConversationNotFound,
    DifyRateLimited,
    DifyUnavailable,
)


def make_client(handler) -> DifyClient:
    return DifyClient(
        base_url="http://dify.test/v1",
        api_key="app-key",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def test_chat_sends_blocking_request_and_parses_answer() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "answer": "Ответ",
                "conversation_id": "conv-1",
                "message_id": "msg-1",
                "metadata": {
                    "retriever_resources": [
                        {"document_name": "Стипендии", "doc_metadata": {"url": "https://utmn.ru/a"}},
                        {"document_name": "Дубль", "doc_metadata": {"url": "https://utmn.ru/a"}},
                        {"document_name": "Без ссылки", "doc_metadata": {}},
                    ]
                },
            },
        )

    result = make_client(handler).chat("Вопрос", user="vk:1", conversation_id="conv-1")

    assert seen["path"] == "/v1/chat-messages"
    assert seen["auth"] == "Bearer app-key"
    assert seen["body"] == {
        "query": "Вопрос",
        "inputs": {},
        "user": "vk:1",
        "response_mode": "blocking",
        "conversation_id": "conv-1",
    }
    assert result.answer == "Ответ"
    assert result.conversation_id == "conv-1"
    assert result.message_id == "msg-1"
    assert [(s.label, s.url) for s in result.sources] == [("Стипендии", "https://utmn.ru/a")]


def test_new_conversation_sends_empty_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["conversation_id"] == ""
        return httpx.Response(200, json={"answer": "a", "conversation_id": "c", "message_id": "m"})

    assert make_client(handler).chat("q", user="max:1").conversation_id == "c"


def test_404_with_conversation_raises_not_found() -> None:
    client = make_client(
        lambda r: httpx.Response(404, json={"code": "not_found", "message": "Conversation Not Exists."})
    )
    with pytest.raises(DifyConversationNotFound):
        client.chat("q", user="vk:1", conversation_id="gone")


@pytest.mark.parametrize(
    ("status", "error"), [(429, DifyRateLimited), (502, DifyUnavailable), (504, DifyUnavailable)]
)
def test_http_errors_are_mapped(status, error) -> None:
    client = make_client(lambda r: httpx.Response(status, json={}))
    with pytest.raises(error):
        client.chat("q", user="vk:1")


def test_send_feedback() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": "success"})

    make_client(handler).send_feedback("msg-1", "like", "vk:1")
    assert seen == {"path": "/v1/messages/msg-1/feedbacks", "body": {"rating": "like", "user": "vk:1"}}
