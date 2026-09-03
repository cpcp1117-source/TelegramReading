from __future__ import annotations

from alembic.command import upgrade
from alembic.config import Config
from uvicorn import run

from telegram_trader.config import get_settings
from telegram_trader.logging_config import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    upgrade(Config("alembic.ini"), "head")
    run(
        "telegram_trader.app:app",
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
