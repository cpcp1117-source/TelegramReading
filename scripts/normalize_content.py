from __future__ import annotations

import argparse

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.normalize_content import run_normalization


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize committed telegram_message_versions rows into normalized_content"
    )
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    try:
        results = run_normalization(
            create_session_factory(engine),
            batch_size=args.batch_size,
            snapshot_max_age_seconds=settings.binance_snapshot_max_age_seconds,
        )
        for result in results:
            print(
                f"channel_id={result.channel_id} topic_id={result.topic_id} "
                f"processed={result.processed_count}"
            )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
