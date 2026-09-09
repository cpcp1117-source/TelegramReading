from __future__ import annotations

import argparse

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.parse_signals import run_signal_parsing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse committed normalized_content rows (EXECUTION_SIGNAL "
        "channels only) into normalized_signals"
    )
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    try:
        results = run_signal_parsing(create_session_factory(engine), batch_size=args.batch_size)
        for result in results:
            print(
                f"channel_id={result.channel_id} topic_id={result.topic_id} "
                f"processed={result.processed_count} skipped={result.skipped_count}"
            )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
