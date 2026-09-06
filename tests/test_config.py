from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from telegram_trader.config import Settings, get_settings


def test_default_configuration_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "APP_DATABASE_URL",
        "APP_DATABASE_HOST",
        "APP_DATABASE_PORT",
        "APP_DATABASE_NAME",
        "APP_DATABASE_USER",
        "APP_DATABASE_PASSWORD",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_PATH",
        "TELEGRAM_TARGET_USERNAME",
        "TELEGRAM_TARGET_CHANNEL_ID",
    ):
        monkeypatch.delenv(variable, raising=False)
    settings = Settings()
    assert settings.environment == "offline"
    assert settings.http_port == 8080
    assert settings.sqlalchemy_database_url.host == "localhost"


def test_external_database_host_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be local"):
        Settings(database_url="postgresql+psycopg://user@example.com/database")


def test_non_postgresql_database_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL only"):
        Settings(database_url="sqlite:///local.db")


def test_database_name_is_required() -> None:
    with pytest.raises(ValidationError, match="database name"):
        Settings(database_url="postgresql+psycopg://postgres@localhost")


def test_database_components_safely_preserve_special_character_password() -> None:
    special_value = "local@pass:/#%?[]!+"
    settings = Settings(
        database_url=None,
        database_host="db",
        database_name="telegram_trader",
        database_user="postgres",
        database_password=SecretStr(special_value),
    )

    database_url = settings.sqlalchemy_database_url

    assert database_url.password == special_value
    assert database_url.host == "db"
    assert special_value not in str(database_url)
    rendered = database_url.render_as_string(hide_password=False)
    assert "local%40pass%3A%2F%23%25%3F%5B%5D%21+" in rendered


def test_blank_component_password_is_rejected() -> None:
    with pytest.raises(ValidationError, match="password cannot be blank"):
        Settings(database_password=SecretStr(""))


def test_cached_settings_can_be_loaded() -> None:
    get_settings.cache_clear()
    assert get_settings().environment == "offline"
    get_settings.cache_clear()


def test_telegram_readonly_requires_both_credentials() -> None:
    with pytest.raises(ValidationError, match="TELEGRAM_API_ID and TELEGRAM_API_HASH"):
        Settings(environment="telegram_readonly")


def test_telegram_credentials_load_from_unprefixed_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    credential_value = "placeholder"
    monkeypatch.setenv("APP_ENVIRONMENT", "telegram_readonly")
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", credential_value)
    monkeypatch.setenv("TELEGRAM_SESSION_PATH", str(tmp_path / "collector"))
    monkeypatch.setenv("TELEGRAM_TARGET_USERNAME", "https://t.me/followgerry")

    settings = Settings()

    assert settings.telegram_api_id == 12345
    assert settings.telegram_api_hash is not None
    assert settings.telegram_api_hash.get_secret_value() == credential_value
    assert credential_value not in repr(settings.telegram_api_hash)
    assert settings.telegram_target_username == "followgerry"


def test_relative_telegram_session_must_be_in_ignored_secrets_directory() -> None:
    with pytest.raises(ValidationError, match="inside secrets"):
        Settings(telegram_session_path=Path("collector"))


def test_phase_2_rejects_another_channel() -> None:
    with pytest.raises(ValidationError, match="only the followgerry"):
        Settings(telegram_target_username="another-channel")


def test_phase_2_rejects_another_channel_id() -> None:
    with pytest.raises(ValidationError, match="only channel ID 2439599598"):
        Settings(telegram_target_channel_id=1)
