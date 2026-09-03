from __future__ import annotations

import pytest
from pydantic import ValidationError

from telegram_trader.config import Settings, get_settings


def test_default_configuration_is_offline() -> None:
    settings = Settings()
    assert settings.environment == "offline"
    assert settings.http_port == 8080


def test_external_database_host_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be local"):
        Settings(database_url="postgresql+psycopg://user@example.com/database")


def test_non_postgresql_database_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL only"):
        Settings(database_url="sqlite:///local.db")


def test_database_name_is_required() -> None:
    with pytest.raises(ValidationError, match="database name"):
        Settings(database_url="postgresql+psycopg://postgres@localhost")


def test_cached_settings_can_be_loaded() -> None:
    get_settings.cache_clear()
    assert get_settings().environment == "offline"
    get_settings.cache_clear()
