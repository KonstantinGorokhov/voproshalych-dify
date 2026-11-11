"""Клиент Service API Dify (приложение типа Chatflow)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx


logger = logging.getLogger(__name__)


class DifyError(Exception):
    """Базовая ошибка обращения к Dify."""


class DifyTimeout(DifyError):
    """Dify не ответил за отведённое время."""


class DifyUnavailable(DifyError):
    """Dify недоступен."""


class DifyRateLimited(DifyError):
    """Dify ограничивает частоту запросов."""


class DifyConversationNotFound(DifyError):
    """Диалог с переданным conversation_id не найден (удалён, сменился ключ и т.п.)."""


@dataclass(slots=True)
class SourceLink:
    """Источник ответа для кнопки-ссылки."""

    label: str
    url: str


@dataclass(slots=True)
class DifyAnswer:
    """Ответ Dify на сообщение пользователя."""

    answer: str
    conversation_id: str
    message_id: str
    sources: list[SourceLink] = field(default_factory=list)


class DifyClient:
    """Синхронный HTTP-клиент Dify Service API.

    POST-запросы не ретраятся автоматически: один вызов — одна попытка.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        source_url_field: str = "url",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._source_url_field = source_url_field
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def chat(
        self,
        query: str,
        user: str,
        conversation_id: str | None = None,
        inputs: dict | None = None,
    ) -> DifyAnswer:
        """Отправляет сообщение в Chatflow и ждёт полный ответ (blocking).

        Args:
            query: Текст вопроса.
            user: Стабильный идентификатор пользователя (`vk:123`).
            conversation_id: Текущий диалог; `None` — начать новый.
            inputs: Значения входных переменных приложения.

        Raises:
            DifyConversationNotFound: Переданный диалог не найден.
            DifyError: Прочие ошибки.
        """

        payload = {
            "query": query,
            "inputs": inputs or {},
            "user": user,
            "response_mode": "blocking",
            "conversation_id": conversation_id or "",
        }
        data = self._post("/chat-messages", payload, has_conversation=bool(conversation_id))
        metadata = data.get("metadata") or {}
        return DifyAnswer(
            answer=str(data.get("answer") or ""),
            conversation_id=str(data.get("conversation_id") or ""),
            message_id=str(data.get("message_id") or data.get("id") or ""),
            sources=self._extract_sources(metadata.get("retriever_resources") or []),
        )

    def send_feedback(self, message_id: str, rating: str | None, user: str) -> None:
        """Отправляет оценку ответа (`like`, `dislike` или `None` — снять оценку)."""

        self._post(
            f"/messages/{message_id}/feedbacks",
            {"rating": rating, "user": user},
        )

    def _extract_sources(self, resources: list) -> list[SourceLink]:
        """Достаёт уникальные ссылки на источники из retriever_resources."""

        links: list[SourceLink] = []
        seen: set[str] = set()
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            doc_metadata = resource.get("doc_metadata") or {}
            url = str(doc_metadata.get(self._source_url_field) or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            label = str(resource.get("document_name") or "Подробнее").strip()
            if len(label) > 60:
                label = label[:57] + "..."
            links.append(SourceLink(label=label, url=url))
        return links

    def _post(self, path: str, payload: dict, has_conversation: bool = False) -> dict:
        """Выполняет POST и переводит ошибки в доменные исключения."""

        try:
            response = self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            logger.warning("Dify timeout on %s", path)
            raise DifyTimeout("Dify timeout") from exc
        except httpx.TransportError as exc:
            logger.warning("Cannot connect to Dify on %s: %s", path, exc)
            raise DifyUnavailable("Cannot connect to Dify") from exc

        if response.is_success:
            try:
                return response.json()
            except ValueError as exc:
                raise DifyError("Dify returned invalid JSON") from exc

        status = response.status_code
        code = ""
        try:
            code = str(response.json().get("code") or "")
        except ValueError:
            pass
        logger.error("Dify HTTP %s on %s: %s", status, path, response.text[:300])

        if status == 404 and has_conversation:
            raise DifyConversationNotFound(code or "conversation not found")
        if status == 429:
            raise DifyRateLimited(code or "rate limited")
        if status in (502, 503, 504):
            raise DifyUnavailable(f"Dify HTTP {status}")
        raise DifyError(f"Dify HTTP {status} {code}".strip())
