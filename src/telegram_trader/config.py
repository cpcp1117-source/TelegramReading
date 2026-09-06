from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

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
    database_url: str | None = None
    database_host: str = "localhost"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "telegram_trader"
    database_user: str = "postgres"
    database_password: SecretStr | None = None
    log_level: LogLevel = "INFO"
    service_name: str = "offline-foundation"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8080, ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def validate_offline_database(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = make_url(value)
        if parsed.drivername not in {"postgresql+psycopg", "postgresql"}:
            raise ValueError("Phase 1 supports PostgreSQL only")
        allowed_hosts = {"localhost", "127.0.0.1", "db", "postgres"}
        if parsed.host not in allowed_hosts:
            raise ValueError("Phase 1 database host must be local or the Compose database service")
        if not parsed.database:
            raise ValueError("database name is required")
        return value

    @field_validator("database_host")
    @classmethod
    def validate_database_host(cls, value: str) -> str:
        allowed_hosts = {"localhost", "127.0.0.1", "db", "postgres"}
        if value not in allowed_hosts:
            raise ValueError("Phase 1 database host must be local or the Compose database service")
        return value

    @field_validator("database_name", "database_user")
    @classmethod
    def validate_non_empty_database_component(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("database name and user are required")
        return value

    @field_validator("database_password")
    @classmethod
    def validate_non_empty_database_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            raise ValueError("database password cannot be blank")
        return value

    @property
    def sqlalchemy_database_url(self) -> URL:
        """Build a URL without treating password characters as URL delimiters."""
        if self.database_url is not None:
            return make_url(self.database_url)
        password = (
            self.database_password.get_secret_value()
            if self.database_password is not None
            else None
        )
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.database_user,
            password=password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
