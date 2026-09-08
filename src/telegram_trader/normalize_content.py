from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import ColumnElement, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.channel_policy import ChannelPolicySnapshot, evaluate_raw_collection
from telegram_trader.models import ChannelPolicy, TelegramMessageVersion
from telegram_trader.models import NormalizedContent as NormalizedContentRow
from telegram_trader.normalization import (
    CURRENT_NORMALIZER_VERSION,
    ChannelSymbolPolicy,
    RawContent,
    normalize_message,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormalizationTarget:
    channel_id: int
    topic_id: int
    symbol_policy: ChannelSymbolPolicy


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    channel_id: int
    topic_id: int
    processed_count: int


def load_normalization_targets(session: Session) -> list[NormalizationTarget]:
    """One target per `channel_policies` row that currently passes `evaluate_raw_collection`.

    A target whose authorization was revoked after collection is skipped
    here (and logged) even though its already-collected raw rows remain in
    `telegram_message_versions` -- normalization stays consistent with
    collection-time gating rather than silently processing revoked content,
    matching the fail-closed spirit of live policy reload.
    """
    targets: list[NormalizationTarget] = []
    for row in session.scalars(select(ChannelPolicy)):
        decision = evaluate_raw_collection(
            ChannelPolicySnapshot(
                channel_id=row.channel_id,
                topic_id=row.topic_id,
                automation_authorization=row.automation_authorization,
                gate_decision=row.gate_decision,
            )
        )
        if not decision.allowed:
            LOGGER.warning(
                "channel policy excludes target from normalization",
                extra={
                    "context": {
                        "channel_id": row.channel_id,
                        "topic_id": row.topic_id,
                        "reason": decision.reason,
                    }
                },
            )
            continue
        targets.append(
            NormalizationTarget(
                channel_id=row.channel_id,
                topic_id=row.topic_id,
                symbol_policy=ChannelSymbolPolicy(
                    channel_id=row.channel_id,
                    topic_id=row.topic_id,
                    symbol_scope_mode=row.symbol_scope_mode,
                    allowed_symbols=frozenset(row.allowed_symbols),
                    prohibited_symbols=frozenset(row.prohibited_symbols),
                ),
            )
        )
    return targets


def _topic_condition(topic_id: int) -> ColumnElement[bool]:
    """`channel_policies.topic_id=0` sentinel <-> `telegram_message_versions.topic_id IS NULL`."""
    if topic_id == 0:
        return TelegramMessageVersion.topic_id.is_(None)
    return TelegramMessageVersion.topic_id == topic_id


def run_normalization(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int = 500,
    version: str = CURRENT_NORMALIZER_VERSION,
) -> list[NormalizationResult]:
    """Normalize committed `telegram_message_versions` rows into `normalized_content`.

    Idempotent: `content_id` is derived from `(raw_message_id, version)` and
    insertion uses `ON CONFLICT DO NOTHING`, so re-running this job is always
    safe. A `NOT IN` subquery over already-normalized `raw_message_id`s for
    the current version means already-normalized history is never re-read.
    """
    with session_factory() as session:
        targets = load_normalization_targets(session)

    results: list[NormalizationResult] = []
    for target in targets:
        processed_count = 0
        while True:
            with session_factory.begin() as session:
                already_normalized = select(NormalizedContentRow.raw_message_id).where(
                    NormalizedContentRow.normalizer_version == version
                )
                candidates = list(
                    session.scalars(
                        select(TelegramMessageVersion)
                        .where(
                            TelegramMessageVersion.channel_id == target.channel_id,
                            _topic_condition(target.topic_id),
                            TelegramMessageVersion.source_event_id.not_in(already_normalized),
                        )
                        .order_by(TelegramMessageVersion.received_at)
                        .limit(batch_size)
                    )
                )
                if not candidates:
                    break
                for row in candidates:
                    result = normalize_message(
                        RawContent(
                            raw_message_id=row.source_event_id,
                            channel_id=row.channel_id,
                            topic_id=row.topic_id,
                            text=row.text,
                            content_type=row.content_type,
                            media_sha256=row.media_sha256,
                        ),
                        target.symbol_policy,
                        version=version,
                    )
                    session.execute(
                        pg_insert(NormalizedContentRow)
                        .values(
                            content_id=result.content_id,
                            raw_message_id=result.raw_message_id,
                            channel_id=result.channel_id,
                            topic_id=result.topic_id,
                            normalizer_version=result.normalizer_version,
                            normalized_text=result.normalized_text,
                            symbol_scope_mode=result.symbol_scope_mode,
                            symbol_candidates=result.symbol_candidates,
                            resolved_symbols=[
                                {"symbol": resolved.symbol, "status": resolved.status}
                                for resolved in result.resolved_symbols
                            ],
                            media_review_status=result.media_review_status,
                            content_hash=result.content_hash,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["raw_message_id", "normalizer_version"]
                        )
                    )
                processed_count += len(candidates)
        results.append(
            NormalizationResult(
                channel_id=target.channel_id,
                topic_id=target.topic_id,
                processed_count=processed_count,
            )
        )
    return results
