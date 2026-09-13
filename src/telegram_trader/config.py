from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

RuntimeEnvironment = Literal["offline", "telegram_readonly", "control_bot"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class TelegramChannelTarget(BaseModel):
    """One Telegram source to collect: a whole channel, or a single forum topic within a group."""

    channel_id: int = Field(gt=0)
    topic_id: int | None = Field(default=None, gt=0)
    username: str | None = None
    label: str

    @property
    def identity(self) -> tuple[int, int | None]:
        return (self.channel_id, self.topic_id)


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
    telegram_target_channels: list[TelegramChannelTarget] = Field(
        default_factory=lambda: [
            TelegramChannelTarget(
                channel_id=2439599598,
                topic_id=None,
                username="followgerry",
                label="Monster-貨幣宇宙中心",
            ),
            TelegramChannelTarget(
                channel_id=2382278102,
                topic_id=21,
                username=None,
                label="邦妮區塊鏈-BTC ETH 即時更新",
            ),
        ],
        validation_alias=AliasChoices("TELEGRAM_TARGET_CHANNELS", "APP_TELEGRAM_TARGET_CHANNELS"),
    )
    policy_poll_interval_seconds: float = Field(
        default=300.0,
        gt=0,
        validation_alias=AliasChoices(
            "POLICY_POLL_INTERVAL_SECONDS", "APP_POLICY_POLL_INTERVAL_SECONDS"
        ),
    )
    control_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CONTROL_BOT_TOKEN", "APP_CONTROL_BOT_TOKEN"),
    )
    control_bot_allowlisted_user_id: int | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices(
            "CONTROL_BOT_ALLOWLISTED_USER_ID", "APP_CONTROL_BOT_ALLOWLISTED_USER_ID"
        ),
    )
    control_bot_session_path: Path = Field(
        default=Path("secrets/telegram/control-bot"),
        validation_alias=AliasChoices("CONTROL_BOT_SESSION_PATH", "APP_CONTROL_BOT_SESSION_PATH"),
    )
    control_bot_poll_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias=AliasChoices(
            "CONTROL_BOT_POLL_INTERVAL_SECONDS", "APP_CONTROL_BOT_POLL_INTERVAL_SECONDS"
        ),
    )
    binance_api_base_url: str = Field(
        default="https://fapi.binance.com",
        validation_alias=AliasChoices("BINANCE_API_BASE_URL", "APP_BINANCE_API_BASE_URL"),
    )
    binance_snapshot_max_age_seconds: float = Field(
        default=21600.0,
        gt=0,
        validation_alias=AliasChoices(
            "BINANCE_SNAPSHOT_MAX_AGE_SECONDS", "APP_BINANCE_SNAPSHOT_MAX_AGE_SECONDS"
        ),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "APP_OPENAI_API_KEY"),
    )
    openai_api_base_url: str = Field(
        default="https://api.openai.com",
        validation_alias=AliasChoices("OPENAI_API_BASE_URL", "APP_OPENAI_API_BASE_URL"),
    )
    openai_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias=AliasChoices(
            "OPENAI_REQUEST_TIMEOUT_SECONDS", "APP_OPENAI_REQUEST_TIMEOUT_SECONDS"
        ),
    )
    thesis_model: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("THESIS_MODEL", "APP_THESIS_MODEL"),
    )
    thesis_batch_size: int = Field(
        default=25,
        gt=0,
        validation_alias=AliasChoices("THESIS_BATCH_SIZE", "APP_THESIS_BATCH_SIZE"),
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

    @field_validator("control_bot_token")
    @classmethod
    def validate_non_empty_control_bot_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("Control Bot token cannot be blank")
        return value

    @field_validator("openai_api_key")
    @classmethod
    def validate_non_empty_openai_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("OpenAI API key cannot be blank")
        return value

    @field_validator("thesis_model")
    @classmethod
    def validate_non_empty_thesis_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("thesis model name cannot be blank")
        return value

    @field_validator("telegram_session_path", "control_bot_session_path")
    @classmethod
    def validate_telegram_session_path(cls, value: Path) -> Path:
        if not value.parts:
            raise ValueError("Telegram session path is required")
        if not value.is_absolute() and value.parts[0].lower() != "secrets":
            raise ValueError("relative Telegram session path must be inside secrets/")
        return value

    @field_validator("telegram_target_channels")
    @classmethod
    def validate_telegram_target_channels(
        cls, value: list[TelegramChannelTarget]
    ) -> list[TelegramChannelTarget]:
        if not value:
            raise ValueError("at least one Telegram target channel is required")
        identities = [target.identity for target in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Telegram target channels must not contain duplicate (channel_id, topic_id) pairs"
            )
        return value

    @model_validator(mode="after")
    def validate_telegram_credentials_for_runtime(self) -> Settings:
        if self.environment in ("telegram_readonly", "control_bot") and (
            self.telegram_api_id is None or self.telegram_api_hash is None
        ):
            raise ValueError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH are required "
                "for telegram_readonly and control_bot"
            )
        if self.environment == "control_bot" and (
            self.control_bot_token is None or self.control_bot_allowlisted_user_id is None
        ):
            raise ValueError(
                "CONTROL_BOT_TOKEN and CONTROL_BOT_ALLOWLISTED_USER_ID are required for control_bot"
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
