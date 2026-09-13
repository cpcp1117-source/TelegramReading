from __future__ import annotations

import argparse

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.openai_client import create_openai_client
from telegram_trader.thesis_extraction import run_thesis_extraction


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract structured Theses from authorized ANALYSIS-channel "
        "normalized_content rows via OpenAI"
    )
    parser.add_argument("--batch-size", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    client = create_openai_client(settings)
    try:
        results = run_thesis_extraction(
            create_session_factory(engine),
            client,
            batch_size=args.batch_size or settings.thesis_batch_size,
            model=settings.thesis_model,
        )
        for result in results:
            print(
                f"channel_id={result.channel_id} topic_id={result.topic_id} "
                f"processed={result.processed_count} skipped={result.skipped_count}"
            )
        return 0
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
