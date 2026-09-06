from __future__ import annotations

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
