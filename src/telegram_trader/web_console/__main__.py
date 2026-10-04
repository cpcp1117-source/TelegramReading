from __future__ import annotations

from uvicorn import run

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine
from telegram_trader.logging_config import configure_logging
from telegram_trader.web_console.app import create_app


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Migrations are owned by the `app` service; the console only reads and writes rows.
    run(
        create_app(settings, create_db_engine(settings)),
        host=settings.http_host,
        port=settings.http_port,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
