from __future__ import annotations

import argparse
from pathlib import Path

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.retention_cleanup import run_retention_cleanup
from telegram_trader.telegram_storage import MediaStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete telegram_message_versions rows (and their media) "
        "past each channel's declared raw_retention_days"
    )
    parser.add_argument("--media-root", type=Path, default=Path("media"))
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    try:
        results = run_retention_cleanup(
            create_session_factory(engine),
            MediaStore(args.media_root),
            batch_size=args.batch_size,
        )
        for result in results:
            print(
                f"channel_id={result.channel_id} topic_id={result.topic_id} "
                f"deleted_rows={result.deleted_row_count} "
                f"deleted_media={result.deleted_media_count} "
                f"failed_media_deletes={result.failed_media_deletes}"
            )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
