from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, exists, select, text
from sqlalchemy import delete as sa_delete
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.models import (
    ChannelPolicy,
    NormalizedSignal,
    OrderLifecycle,
    TelegramMessageVersion,
    TradeIntent,
)
from telegram_trader.telegram_storage import MediaStore

LOGGER = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 24 * 3600
FIRST_CLEANUP_DELAY_SECONDS = 60


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


def _led_to_an_order() -> ColumnElement[bool]:
    """The message produced a signal whose approval was actually executed.

    Deleting it would CASCADE through the signal and its approval into
    `trade_intent` and every order row; those are kept (user decision
    2026-10-04), and migration 0022's trigger refuses such a delete anyway.
    A risk-rejected intent never reached `order_lifecycle`, so it is not kept.
    """
    return exists(
        select(1)
        .select_from(NormalizedSignal)
        .join(TradeIntent, TradeIntent.signal_row_id == NormalizedSignal.signal_row_id)
        .join(OrderLifecycle, OrderLifecycle.intent_id == TradeIntent.intent_id)
        .where(NormalizedSignal.raw_message_id == TelegramMessageVersion.source_event_id)
    )


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

    A message whose signal led to an order is skipped (see `_led_to_an_order`),
    so its signal, approval and order records are all kept.
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
                        ~_led_to_an_order(),
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


def retention_migration_applied(session_factory: sessionmaker[Session]) -> bool:
    """True once migration 0022 is in the database.

    Guards the automatic cleanup: a collector started on new code against a
    database still on the old 7-day `raw_retention_days` would otherwise
    delete everything older than a week on its first run.
    """
    with session_factory() as session:
        return bool(
            session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                    "WHERE tgname = 'trade_intent_no_delete_with_orders')"
                )
            )
        )


async def run_retention_forever(
    session_factory: sessionmaker[Session],
    media_store: MediaStore,
    *,
    interval_seconds: float = CLEANUP_INTERVAL_SECONDS,
    first_delay_seconds: float = FIRST_CLEANUP_DELAY_SECONDS,
    cleanup: Callable[..., list[RetentionCleanupResult]] = run_retention_cleanup,
    migration_applied: Callable[[sessionmaker[Session]], bool] = retention_migration_applied,
) -> None:
    """Run the cleanup shortly after start, then once per interval, until cancelled.

    Runs inside the collector, the only service that mounts the media folder.
    The blocking DB/file work goes to a worker thread so Telegram message
    handling on the event loop is never stalled. A failed run is logged and
    simply retried at the next interval -- nothing is lost by waiting a day.
    """
    await asyncio.sleep(first_delay_seconds)
    while True:
        try:
            if not await asyncio.to_thread(migration_applied, session_factory):
                LOGGER.warning(
                    "retention cleanup skipped: migration 0022 (60-day retention) is not "
                    "applied yet, so channels may still say 7 days"
                )
                await asyncio.sleep(interval_seconds)
                continue
            results = await asyncio.to_thread(cleanup, session_factory, media_store)
            LOGGER.info(
                "retention cleanup finished",
                extra={
                    "context": {
                        "deleted_rows": sum(r.deleted_row_count for r in results),
                        "deleted_media": sum(r.deleted_media_count for r in results),
                        "failed_media_deletes": sum(r.failed_media_deletes for r in results),
                    }
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("retention cleanup failed; retrying at the next interval")
        await asyncio.sleep(interval_seconds)
