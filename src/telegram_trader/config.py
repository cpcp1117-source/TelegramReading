from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

RuntimeEnvironment = Literal["offline", "telegram_readonly"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Stage-gated configuration for offline and Telegram read-only operation."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        case_sensitive=False,
        extra="forbid",
        populate_by_name=True,
    )

    environment: RuntimeEnvironment = "offline"
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
    telegram_api_id: int | None = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("TELEGRAM_API_ID", "APP_TELEGRAM_API_ID"),
    )
    telegram_api_hash: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_API_HASH", "APP_TELEGRAM_API_HASH"),
    )
    telegram_session_path: Path = Field(
        default=Path("secrets/telegram/collector"),
        validation_alias=AliasChoices("TELEGRAM_SESSION_PATH", "APP_TELEGRAM_SESSION_PATH"),
    )
    telegram_target_username: str = Field(
        default="followgerry",
        validation_alias=AliasChoices("TELEGRAM_TARGET_USERNAME", "APP_TELEGRAM_TARGET_USERNAME"),
    )
    telegram_target_channel_id: int = Field(
        default=2439599598,
        ge=1,
        validation_alias=AliasChoices(
            "TELEGRAM_TARGET_CHANNEL_ID", "APP_TELEGRAM_TARGET_CHANNEL_ID"
        ),
    )

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

    @field_validator("telegram_api_hash")
    @classmethod
    def validate_non_empty_telegram_api_hash(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("Telegram API hash cannot be blank")
        return value

    @field_validator("telegram_session_path")
    @classmethod
    def validate_telegram_session_path(cls, value: Path) -> Path:
        if not value.parts:
            raise ValueError("Telegram session path is required")
        if not value.is_absolute() and value.parts[0].lower() != "secrets":
            raise ValueError("relative Telegram session path must be inside secrets/")
        return value

    @field_validator("telegram_target_username")
    @classmethod
    def validate_phase_2_target(cls, value: str) -> str:
        normalized = value.strip().removeprefix("https://t.me/").removeprefix("@").lower()
        if normalized != "followgerry":
            raise ValueError("Phase 2 permits only the followgerry channel")
        return normalized

    @field_validator("telegram_target_channel_id")
    @classmethod
    def validate_phase_2_target_id(cls, value: int) -> int:
        if value != 2439599598:
            raise ValueError("Phase 2 permits only channel ID 2439599598")
        return value

    @model_validator(mode="after")
    def validate_telegram_credentials_for_runtime(self) -> Settings:
        if self.environment == "telegram_readonly" and (
            self.telegram_api_id is None or self.telegram_api_hash is None
        ):
            raise ValueError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH are required for telegram_readonly"
            )
        return self

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
