from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OfflineEnvironment = Literal["offline"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Phase 1 configuration with an intentionally offline-only environment."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        case_sensitive=False,
        extra="forbid",
    )

    environment: OfflineEnvironment = "offline"
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/telegram_trader"
    log_level: LogLevel = "INFO"
    service_name: str = "offline-foundation"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8080, ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def validate_offline_database(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"postgresql+psycopg", "postgresql"}:
            raise ValueError("Phase 1 supports PostgreSQL only")
        allowed_hosts = {"localhost", "127.0.0.1", "db", "postgres"}
        if parsed.hostname not in allowed_hosts:
            raise ValueError("Phase 1 database host must be local or the Compose database service")
        if not parsed.path or parsed.path == "/":
            raise ValueError("database name is required")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
