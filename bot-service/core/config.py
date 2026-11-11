"""Объекты конфигурации для core-сервиса."""

from dataclasses import dataclass
import os


@dataclass(slots=True)
class Settings:
    """Настройки запуска FastAPI core-сервиса."""

    app_name: str = os.getenv("BOT_CORE_APP_NAME", "bot-core")
    app_version: str = os.getenv("BOT_CORE_APP_VERSION", "0.2.0")
    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: str = os.getenv("POSTGRES_PORT", "5432")
    postgres_db: str = os.getenv("POSTGRES_DB", "voproshalych")
    postgres_user: str = os.getenv("POSTGRES_USER", "voproshalych")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "voproshalych")
    # Базовый URL Service API Dify, например https://dify.example.ru/v1
    dify_api_url: str = os.getenv("DIFY_API_URL", "http://localhost/v1")
    # API-ключ приложения Chatflow (app-...)
    dify_api_key: str = os.getenv("DIFY_API_KEY", "")
    dify_timeout_seconds: float = float(os.getenv("DIFY_TIMEOUT_SECONDS", "280"))
    # Поле метаданных документа базы знаний Dify, в котором лежит ссылка на источник
    dify_source_url_field: str = os.getenv("DIFY_SOURCE_URL_FIELD", "url")


settings = Settings()
