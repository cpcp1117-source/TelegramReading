from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

RuntimeEnvironment = Literal[
    "offline", "telegram_readonly", "control_bot", "execution_gateway", "web_console"
]
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
            TelegramChannelTarget(
                channel_id=3182714504,
                topic_id=7,
                username=None,
                label="邦妮區塊鏈 VIP專屬CLUB (幣種分析)",
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
    # Phase 6 Slice 2a -- Execution Gateway only. Per credential-handoff.md §5, this
    # key is scoped to the execution_gateway environment; other services never get
    # a real value even though the field exists on this shared Settings class (they
    # simply never receive it via Compose env passthrough).
    binance_testnet_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("BINANCE_TESTNET_API_KEY", "APP_BINANCE_TESTNET_API_KEY"),
    )
    binance_testnet_api_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BINANCE_TESTNET_API_SECRET", "APP_BINANCE_TESTNET_API_SECRET"
        ),
    )
    binance_testnet_base_url: str = Field(
        default="https://demo-fapi.binance.com",
        validation_alias=AliasChoices("BINANCE_TESTNET_BASE_URL", "APP_BINANCE_TESTNET_BASE_URL"),
    )
    # Hardcoded to the only value that may exist before Gate 7/8 -- there is no
    # Production key yet, so Production is not even a selectable value (TM-012).
    execution_environment: Literal["TESTNET"] = Field(
        default="TESTNET",
        validation_alias=AliasChoices("EXECUTION_ENVIRONMENT", "APP_EXECUTION_ENVIRONMENT"),
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
    # Analysis-setup pipeline (AI turns an ANALYSIS-channel message into a
    # monitorable setup; code, not the AI, watches the market and decides when
    # a draft is raised). `ai_provider="codex"` runs the locally logged-in
    # Codex CLI instead of calling the OpenAI HTTP API, so no API key is needed.
    ai_provider: Literal["openai", "codex"] = Field(
        default="openai",
        validation_alias=AliasChoices("AI_PROVIDER", "APP_AI_PROVIDER"),
    )
    codex_cli_path: str = Field(
        default="codex",
        validation_alias=AliasChoices("CODEX_CLI_PATH", "APP_CODEX_CLI_PATH"),
    )
    ai_cli_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        validation_alias=AliasChoices("AI_CLI_TIMEOUT_SECONDS", "APP_AI_CLI_TIMEOUT_SECONDS"),
    )
    setup_model: str = Field(
        default="gpt-5.6-sol",
        validation_alias=AliasChoices("SETUP_MODEL", "APP_SETUP_MODEL"),
    )
    setup_batch_size: int = Field(
        default=10,
        gt=0,
        validation_alias=AliasChoices("SETUP_BATCH_SIZE", "APP_SETUP_BATCH_SIZE"),
    )
    setup_context_message_count: int = Field(
        default=8,
        ge=0,
        validation_alias=AliasChoices(
            "SETUP_CONTEXT_MESSAGE_COUNT", "APP_SETUP_CONTEXT_MESSAGE_COUNT"
        ),
    )
    setup_expiry_hours: int = Field(
        default=48,
        gt=0,
        validation_alias=AliasChoices("SETUP_EXPIRY_HOURS", "APP_SETUP_EXPIRY_HOURS"),
    )
    # Suggested size (fraction of equity used as margin) for drafts built from analysis setups;
    # smaller than the usual 20% because these are discretionary, conditional ideas. The user can
    # still change it per trade before approving.
    setup_default_position_size_pct: Decimal = Field(
        default=Decimal("0.10"),
        gt=0,
        le=1,
        validation_alias=AliasChoices(
            "SETUP_DEFAULT_POSITION_SIZE_PCT", "APP_SETUP_DEFAULT_POSITION_SIZE_PCT"
        ),
    )
    setup_confirm_interval: Literal["5m", "15m", "30m", "1h"] = Field(
        default="15m",
        validation_alias=AliasChoices("SETUP_CONFIRM_INTERVAL", "APP_SETUP_CONFIRM_INTERVAL"),
    )
    # Phase 6 Slice 1 -- Risk Engine (FR-016). Numeric defaults below are the
    # spec's own fixed BR-006/007/008/009/010/011 values, not arbitrary --
    # see system-spec.md. `risk_equity_baseline_usdt` has no sensible
    # universal default (it's a real account-shaped number) and is
    # deliberately required, not defaulted -- same convention as
    # `POSTGRES_PASSWORD` having no default.
    risk_leverage: int = Field(
        default=5, gt=0, validation_alias=AliasChoices("RISK_LEVERAGE", "APP_RISK_LEVERAGE")
    )
    risk_default_stop_roe_pct: Decimal = Field(
        default=Decimal("0.30"),
        gt=0,
        validation_alias=AliasChoices("RISK_DEFAULT_STOP_ROE_PCT", "APP_RISK_DEFAULT_STOP_ROE_PCT"),
    )
    risk_position_size_pct: Decimal = Field(
        default=Decimal("0.20"),
        gt=0,
        validation_alias=AliasChoices("RISK_POSITION_SIZE_PCT", "APP_RISK_POSITION_SIZE_PCT"),
    )
    risk_max_concurrent_positions: int = Field(
        default=3,
        gt=0,
        validation_alias=AliasChoices(
            "RISK_MAX_CONCURRENT_POSITIONS", "APP_RISK_MAX_CONCURRENT_POSITIONS"
        ),
    )
    risk_daily_loss_kill_switch_pct: Decimal = Field(
        default=Decimal("0.06"),
        gt=0,
        validation_alias=AliasChoices(
            "RISK_DAILY_LOSS_KILL_SWITCH_PCT", "APP_RISK_DAILY_LOSS_KILL_SWITCH_PCT"
        ),
    )
    risk_max_receive_lag_seconds: int = Field(
        default=10,
        gt=0,
        validation_alias=AliasChoices(
            "RISK_MAX_RECEIVE_LAG_SECONDS", "APP_RISK_MAX_RECEIVE_LAG_SECONDS"
        ),
    )
    risk_max_price_deviation_bps: int = Field(
        default=50,
        gt=0,
        validation_alias=AliasChoices(
            "RISK_MAX_PRICE_DEVIATION_BPS", "APP_RISK_MAX_PRICE_DEVIATION_BPS"
        ),
    )
    risk_equity_baseline_usdt: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("RISK_EQUITY_BASELINE_USDT", "APP_RISK_EQUITY_BASELINE_USDT"),
    )
    risk_decision_expiry_seconds: float = Field(
        default=300.0,
        gt=0,
        validation_alias=AliasChoices(
            "RISK_DECISION_EXPIRY_SECONDS", "APP_RISK_DECISION_EXPIRY_SECONDS"
        ),
    )

    # Web Console -- the only credentials it holds are its own login; it never
    # receives an exchange, Telegram or OpenAI secret (ADR-0002).
    web_console_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WEB_CONSOLE_USERNAME", "APP_WEB_CONSOLE_USERNAME"),
    )
    web_console_password_hash: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("WEB_CONSOLE_PASSWORD_HASH", "APP_WEB_CONSOLE_PASSWORD_HASH"),
    )
    web_console_session_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "WEB_CONSOLE_SESSION_SECRET", "APP_WEB_CONSOLE_SESSION_SECRET"
        ),
    )
    web_console_session_hours: float = Field(
        default=12.0,
        gt=0,
        validation_alias=AliasChoices("WEB_CONSOLE_SESSION_HOURS", "APP_WEB_CONSOLE_SESSION_HOURS"),
    )

    @field_validator("web_console_username", mode="before")
    @classmethod
    def blank_web_console_username_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("risk_equity_baseline_usdt", mode="before")
    @classmethod
    def blank_risk_equity_baseline_to_none(cls, value: object) -> object:
        """A present-but-blank env var (e.g. Compose's `${VAR:-}` passthrough of an

        unset `.env` key) must mean "not configured", the same as the key
        being absent entirely -- not a decimal-parsing error on `""`. Must
        run `mode="before"`: Pydantic's own `Decimal` coercion would reject
        an empty string before any `mode="after"` validator ever saw it.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("risk_equity_baseline_usdt")
    @classmethod
    def validate_risk_equity_baseline(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("risk equity baseline must be positive")
        return value

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
        """Deliberately still rejects blank rather than coercing to `None`

        (unlike the Telegram/Control Bot/OpenAI secrets below): a database
        password silently becoming "unset" is a materially different, more
        consequential failure mode than an unused AI credential being unset.
        Docker Compose's own `${POSTGRES_PASSWORD:?...}` required
        substitution already guards the real deployment path before this
        even runs; this validator is the defense-in-depth backstop for
        anything constructing `Settings` directly.
        """
        if value is not None and not value.get_secret_value():
            raise ValueError("database password cannot be blank")
        return value

    @field_validator(
        "telegram_api_hash",
        "control_bot_token",
        "openai_api_key",
        "binance_testnet_api_key",
        "binance_testnet_api_secret",
        "web_console_password_hash",
        "web_console_session_secret",
    )
    @classmethod
    def blank_optional_secret_to_none(cls, value: SecretStr | None) -> SecretStr | None:
        """A present-but-blank secret (e.g. an unset `.env` key passed through

        Compose's `${VAR:-}`) means "not configured", same as an absent key
        -- never a validation error here. Whatever actually *requires* the
        value present (the `model_validator` below, for the environments
        that need it) still enforces that in its own context; this just
        stops an unrelated context (e.g. `app` running `environment=offline`,
        which never needs `OPENAI_API_KEY`) from being broken by a field it
        doesn't even use.
        """
        if value is not None and not value.get_secret_value().strip():
            return None
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
        if self.environment == "execution_gateway" and (
            self.binance_testnet_api_key is None or self.binance_testnet_api_secret is None
        ):
            raise ValueError(
                "BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET are required "
                "for execution_gateway"
            )
        if self.environment == "web_console":
            if (
                self.web_console_username is None
                or self.web_console_password_hash is None
                or self.web_console_session_secret is None
            ):
                raise ValueError(
                    "WEB_CONSOLE_USERNAME, WEB_CONSOLE_PASSWORD_HASH and "
                    "WEB_CONSOLE_SESSION_SECRET are required for web_console"
                )
            if len(self.web_console_session_secret.get_secret_value()) < 32:
                raise ValueError("WEB_CONSOLE_SESSION_SECRET must be at least 32 characters")
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
