from __future__ import annotations

import argparse

from telegram_trader.binance_market_data import create_market_data_client, refresh_snapshot
from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Binance USD(S)-M futures exchangeInfo and append a new "
        "binance_symbol_snapshots row (no API key needed -- public market data only)"
    )
    parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    client = create_market_data_client(settings)
    try:
        snapshot = refresh_snapshot(client, create_session_factory(engine))
        print(
            f"snapshot_id={snapshot.snapshot_id} fetched_at={snapshot.fetched_at.isoformat()} "
            f"symbol_count={snapshot.symbol_count}"
        )
        return 0
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
