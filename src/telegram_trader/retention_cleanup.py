from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, select, text
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.models import ChannelPolicy, TelegramMessageVersion
from telegram_trader.telegram_storage import MediaStore

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetentionTarget:
    channel_id: int
    topic_id: int
    raw_retention_days: int


@dataclass(frozen=True, slots=True)
class RetentionCleanupResult:
    channel_id: int
    topic_id: int
    deleted_row_count: int
    deleted_media_count: int
    failed_media_deletes: int


def load_retention_targets(session: Session) -> list[RetentionTarget]:
    """One target per `channel_policies` row, regardless of `gate_decision` —

    already-collected raw data still ages out even for a paused/rejected
    channel. A `(channel_id, topic_id)` with no policy row is never touched
    by cleanup (fails closed): the trigger's own server-side lookup would
    reject it too, since `raw_retention_days` would resolve to NULL.
    """
    return [
        RetentionTarget(row.channel_id, row.topic_id, row.raw_retention_days)
        for row in session.scalars(select(ChannelPolicy))
    ]


def _topic_condition(topic_id: int) -> ColumnElement[bool]:
    """`channel_policies.topic_id=0` sentinel <-> `telegram_message_versions.topic_id IS NULL`."""
    if topic_id == 0:
        return TelegramMessageVersion.topic_id.is_(None)
    return TelegramMessageVersion.topic_id == topic_id


def run_retention_cleanup(
    session_factory: sessionmaker[Session],
    media_store: MediaStore,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    batch_size: int = 500,
) -> list[RetentionCleanupResult]:
    """Delete `telegram_message_versions` rows (and their media) past each

    channel's declared `raw_retention_days`. Age is measured from
    `received_at` (when we ingested it), not `source_date` (Telegram's
    original timestamp) — otherwise a freshly-backfilled old message would
    be instantly eligible for deletion. `telegram_collector_checkpoints` is
    never touched: checkpoints must stay monotonic, or the collector would
    re-backfill (and effectively revive) purged data on its next restart.

    Each batch is its own committed transaction; `SET LOCAL` is
    transaction-scoped and never leaks the bypass onto a reused pooled
    connection. Media files are deleted only after the DB commit succeeds —
    an orphaned file on disk is a strictly safer failure mode than deleting
    the file before the row deletion is durable.
    """
    with session_factory() as session:
        targets = load_retention_targets(session)

    results: list[RetentionCleanupResult] = []
    for target in targets:
        cutoff = now() - timedelta(days=target.raw_retention_days)
        deleted_row_count = 0
        deleted_media_count = 0
        failed_media_deletes = 0

        while True:
            with session_factory.begin() as session:
                session.execute(text("SET LOCAL app.retention_cleanup = 'on'"))
                candidates = session.execute(
                    select(
                        TelegramMessageVersion.source_event_id,
                        TelegramMessageVersion.media_path,
                    )
                    .where(
                        TelegramMessageVersion.channel_id == target.channel_id,
                        _topic_condition(target.topic_id),
                        TelegramMessageVersion.received_at < cutoff,
                    )
                    .order_by(TelegramMessageVersion.received_at)
                    .limit(batch_size)
                ).all()
                if not candidates:
                    break
                ids = [row.source_event_id for row in candidates]
                session.execute(
                    sa_delete(TelegramMessageVersion).where(
                        TelegramMessageVersion.source_event_id.in_(ids)
                    )
                )

            deleted_row_count += len(candidates)
            for row in candidates:
                if row.media_path is None:
                    continue
                try:
                    media_store.delete(row.media_path)
                    deleted_media_count += 1
                except OSError:
                    LOGGER.exception(
                        "retention cleanup could not delete media file",
                        extra={"context": {"media_path": row.media_path}},
                    )
                    failed_media_deletes += 1

        results.append(
            RetentionCleanupResult(
                channel_id=target.channel_id,
                topic_id=target.topic_id,
                deleted_row_count=deleted_row_count,
                deleted_media_count=deleted_media_count,
                failed_media_deletes=failed_media_deletes,
            )
        )
    return results
