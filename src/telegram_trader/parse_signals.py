from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.channel_policy import ChannelPolicySnapshot, evaluate_raw_collection
from telegram_trader.models import ChannelPolicy, SignalParseCheckpoint, TelegramMessageVersion
from telegram_trader.models import NormalizedContent as NormalizedContentRow
from telegram_trader.models import NormalizedSignal as NormalizedSignalRow
from telegram_trader.normalization import CURRENT_NORMALIZER_VERSION
from telegram_trader.signal_parser import (
    CURRENT_PARSER_VERSION,
    EvidenceSpan,
    ParsedFields,
    Side,
    determine_status,
    parse_fields,
    resolve_stop,
    single_resolved_symbol,
)

LOGGER = logging.getLogger(__name__)

_EXPIRY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class SignalTarget:
    channel_id: int
    topic_id: int  # channel_policies' 0-sentinel convention


@dataclass(frozen=True, slots=True)
class SignalParsingResult:
    channel_id: int
    topic_id: int
    processed_count: int
    skipped_count: int


def load_signal_targets(session: Session) -> list[SignalTarget]:
    """One target per `channel_policies` row where `channel_type == 'EXECUTION_SIGNAL'`

    and current authorization allows raw collection (BR-001: only
    EXECUTION_SIGNAL channels get parsed; ANALYSIS channels, e.g.
    bonnie-blockchain, are Phase 5's "thesis" territory and are never
    touched here). Fail-closed on revoked authorization, same as
    `normalize_content.py`'s target loading.
    """
    targets: list[SignalTarget] = []
    for row in session.scalars(
        select(ChannelPolicy).where(ChannelPolicy.channel_type == "EXECUTION_SIGNAL")
    ):
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
                "channel policy excludes target from signal parsing",
                extra={
                    "context": {
                        "channel_id": row.channel_id,
                        "topic_id": row.topic_id,
                        "reason": decision.reason,
                    }
                },
            )
            continue
        targets.append(SignalTarget(channel_id=row.channel_id, topic_id=row.topic_id))
    return targets


def _topic_condition(column: Any, topic_id: int) -> ColumnElement[bool]:
    """`channel_policies.topic_id=0` sentinel <-> a nullable topic_id column."""
    if topic_id == 0:
        return cast("ColumnElement[bool]", column.is_(None))
    return cast("ColumnElement[bool]", column == topic_id)


def _derive_signal_id(raw_message_id: str) -> str:
    return hashlib.sha256(f"signal:{raw_message_id}".encode()).hexdigest()


def _signal_row_id(raw_message_id: str, parser_version: str) -> str:
    return hashlib.sha256(f"{raw_message_id}:{parser_version}".encode()).hexdigest()


def _compute_expires_at(source_date: datetime) -> datetime:
    """A deterministic placeholder, not enforced by anything in this slice --

    recorded for a future consumer (Control Bot/Risk Engine) to check
    freshness against, same as `raw_retention_days` was captured in Phase 3
    before anything consumed it.
    """
    return source_date + _EXPIRY_WINDOW


def _encode_decimals(values: Sequence[Decimal]) -> list[str]:
    """JSONB storage for decimal lists: strings, never floats -- Postgres's own

    `numeric` precision only survives a JSON round-trip through Python when
    the value is a string; psycopg's JSON encoder cannot serialize `Decimal`
    directly, and a `float` would silently lose precision (logical-data-model
    §3.4: "Never floating binary").
    """
    return [str(value) for value in values]


def _decode_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _spans_dict(parsed: ParsedFields) -> dict[str, list[list[int]]]:
    spans: dict[str, list[list[int]]] = {}

    def add(name: str, values: list[EvidenceSpan]) -> None:
        if values:
            spans[name] = [[span.start, span.end] for span in values]

    if parsed.side_span is not None:
        add("side", [parsed.side_span])
    add("entry", parsed.entry_spans)
    if parsed.stop_span is not None:
        add("stop", [parsed.stop_span])
    add("take_profit", parsed.take_profit_spans)
    if parsed.cancel_span is not None:
        add("cancel", [parsed.cancel_span])
    return spans


def _find_parent(
    session: Session,
    channel_id: int,
    topic_id: int,
    reply_to_message_id: int,
    parser_version: str,
) -> NormalizedSignalRow | None:
    parent_raw_message_id = session.scalar(
        select(TelegramMessageVersion.source_event_id)
        .where(
            TelegramMessageVersion.channel_id == channel_id,
            _topic_condition(TelegramMessageVersion.topic_id, topic_id),
            TelegramMessageVersion.message_id == reply_to_message_id,
        )
        .order_by(TelegramMessageVersion.edit_version.desc())
        .limit(1)
    )
    if parent_raw_message_id is None:
        return None
    return session.scalar(
        select(NormalizedSignalRow).where(
            NormalizedSignalRow.raw_message_id == parent_raw_message_id,
            NormalizedSignalRow.parser_version == parser_version,
        )
    )


def _next_revision(session: Session, signal_id: str, parser_version: str) -> int:
    """Revision numbering is scoped per `parser_version`: `signal_id` stays a stable

    identity across a version bump (same convention as `raw_message_id` staying
    stable across a `normalizer_version` bump), but each version reprocesses
    its own independent revision sequence -- `uq_normalized_signal_id_revision`
    is `(signal_id, revision, parser_version)`, not just `(signal_id, revision)`.
    """
    current_max = session.scalar(
        select(func.max(NormalizedSignalRow.revision)).where(
            NormalizedSignalRow.signal_id == signal_id,
            NormalizedSignalRow.parser_version == parser_version,
        )
    )
    return (current_max or 0) + 1


def _insert(
    session: Session,
    *,
    raw_message_id: str,
    signal_id: str,
    revision: int,
    normalizer_version: str,
    channel_id: int | None,
    topic_id: int | None,
    status: str,
    symbol: str | None,
    side: str | None,
    entry_type: str | None,
    entry_values: Sequence[object],
    stop_value: object,
    stop_origin: str,
    take_profits: Sequence[object],
    evidence_spans: dict[str, list[list[int]]],
    link_method: str | None,
    parser_version: str,
    expires_at: datetime,
) -> None:
    session.execute(
        pg_insert(NormalizedSignalRow)
        .values(
            signal_row_id=_signal_row_id(raw_message_id, parser_version),
            signal_id=signal_id,
            revision=revision,
            raw_message_id=raw_message_id,
            normalizer_version=normalizer_version,
            channel_id=channel_id,
            topic_id=topic_id,
            status=status,
            symbol=symbol,
            side=side,
            entry_type=entry_type,
            entry_values=entry_values,
            stop_value=stop_value,
            stop_origin=stop_origin,
            take_profits=take_profits,
            evidence_spans=evidence_spans,
            link_method=link_method,
            parser_version=parser_version,
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(index_elements=["raw_message_id", "parser_version"])
    )


def _process_one(
    session: Session,
    content_row: NormalizedContentRow,
    raw_row: TelegramMessageVersion,
    target: SignalTarget,
    version: str,
) -> bool:
    """Parse one (normalized_content, telegram_message_version) pair.

    Returns True if a `normalized_signals` row was inserted, False if the
    message correctly produced no signal (not a signal attempt at all).
    """
    parsed = parse_fields(content_row.normalized_text)
    resolution = single_resolved_symbol(content_row.resolved_symbols)
    symbol = resolution[0] if resolution else None
    symbol_status = resolution[1] if resolution else None

    parent = None
    if raw_row.reply_to_message_id is not None:
        parent = _find_parent(
            session, target.channel_id, target.topic_id, raw_row.reply_to_message_id, version
        )

    if parsed.cancel_detected and parent is not None:
        _insert(
            session,
            raw_message_id=raw_row.source_event_id,
            signal_id=parent.signal_id,
            revision=_next_revision(session, parent.signal_id, version),
            normalizer_version=content_row.normalizer_version,
            channel_id=content_row.channel_id,
            topic_id=content_row.topic_id,
            status="CANCELLED",
            symbol=parent.symbol,
            side=parent.side,
            entry_type=parent.entry_type,
            entry_values=parent.entry_values,
            stop_value=parent.stop_value,
            stop_origin=parent.stop_origin,
            take_profits=parent.take_profits,
            evidence_spans=_spans_dict(parsed),
            link_method="REPLY",
            parser_version=version,
            expires_at=_compute_expires_at(raw_row.source_date),
        )
        return True

    if (
        parent is not None
        and not parsed.cancel_detected
        and (parsed.stop_value is not None or parsed.take_profits)
    ):
        # A reply that only restates SL/TP: carry the parent's symbol/side/
        # entry and status forward unchanged (an SL/TP update alone cannot
        # make an already-INCOMPLETE signal valid, nor does it need to
        # re-earn a VALIDATED/NEW verdict that was already established).
        entry_reference = _decode_decimal(parent.entry_values[0]) if parent.entry_values else None
        parent_side = cast("Side | None", parent.side)
        stop_value, stop_origin = resolve_stop(parsed.stop_value, parent_side, entry_reference)
        take_profits = (
            _encode_decimals(parsed.take_profits) if parsed.take_profits else parent.take_profits
        )
        _insert(
            session,
            raw_message_id=raw_row.source_event_id,
            signal_id=parent.signal_id,
            revision=_next_revision(session, parent.signal_id, version),
            normalizer_version=content_row.normalizer_version,
            channel_id=content_row.channel_id,
            topic_id=content_row.topic_id,
            status=parent.status,
            symbol=parent.symbol,
            side=parent.side,
            entry_type=parent.entry_type,
            entry_values=parent.entry_values,
            stop_value=stop_value,
            stop_origin=stop_origin,
            take_profits=take_profits,
            evidence_spans=_spans_dict(parsed),
            link_method="REPLY",
            parser_version=version,
            expires_at=_compute_expires_at(raw_row.source_date),
        )
        return True

    if not parsed.is_signal_attempt:
        return False

    entry_reference = parsed.entry_values[0] if parsed.entry_values else None
    stop_value, stop_origin = resolve_stop(parsed.stop_value, parsed.side, entry_reference)
    status = determine_status(symbol_status, parsed.side)
    _insert(
        session,
        raw_message_id=raw_row.source_event_id,
        signal_id=_derive_signal_id(raw_row.source_event_id),
        revision=0,
        normalizer_version=content_row.normalizer_version,
        channel_id=content_row.channel_id,
        topic_id=content_row.topic_id,
        status=status,
        symbol=symbol,
        side=parsed.side,
        entry_type=parsed.entry_type,
        entry_values=_encode_decimals(parsed.entry_values),
        stop_value=stop_value,
        stop_origin=stop_origin,
        take_profits=_encode_decimals(parsed.take_profits),
        evidence_spans=_spans_dict(parsed),
        link_method=None,
        parser_version=version,
        expires_at=_compute_expires_at(raw_row.source_date),
    )
    return True


def run_signal_parsing(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int = 500,
    version: str = CURRENT_PARSER_VERSION,
    normalizer_version: str = CURRENT_NORMALIZER_VERSION,
) -> list[SignalParsingResult]:
    """Parse committed `normalized_content` rows (EXECUTION_SIGNAL channels only)

    into `normalized_signals`. Progress is tracked via `SignalParseCheckpoint`
    rather than "row exists" idempotency, since a message may legitimately
    produce no signal row at all -- see that model's docstring.
    """
    with session_factory() as session:
        targets = load_signal_targets(session)

    results: list[SignalParsingResult] = []
    for target in targets:
        processed_count = 0
        skipped_count = 0
        while True:
            with session_factory.begin() as session:
                checkpoint = session.get(
                    SignalParseCheckpoint, (target.channel_id, target.topic_id, version)
                )
                query = (
                    select(NormalizedContentRow, TelegramMessageVersion)
                    .join(
                        TelegramMessageVersion,
                        NormalizedContentRow.raw_message_id
                        == TelegramMessageVersion.source_event_id,
                    )
                    .where(
                        NormalizedContentRow.channel_id == target.channel_id,
                        _topic_condition(NormalizedContentRow.topic_id, target.topic_id),
                        NormalizedContentRow.normalizer_version == normalizer_version,
                    )
                )
                if checkpoint is not None:
                    query = query.where(
                        (TelegramMessageVersion.received_at > checkpoint.last_received_at)
                        | (
                            (TelegramMessageVersion.received_at == checkpoint.last_received_at)
                            & (NormalizedContentRow.raw_message_id > checkpoint.last_raw_message_id)
                        )
                    )
                rows = session.execute(
                    query.order_by(
                        TelegramMessageVersion.received_at, NormalizedContentRow.raw_message_id
                    ).limit(batch_size)
                ).all()
                if not rows:
                    break

                last_received_at = (
                    checkpoint.last_received_at if checkpoint else datetime.min.replace(tzinfo=UTC)
                )
                last_raw_message_id = checkpoint.last_raw_message_id if checkpoint else ""
                for content_row, raw_row in rows:
                    if _process_one(session, content_row, raw_row, target, version):
                        processed_count += 1
                    else:
                        skipped_count += 1
                    last_received_at = raw_row.received_at
                    last_raw_message_id = raw_row.source_event_id

                if checkpoint is None:
                    session.add(
                        SignalParseCheckpoint(
                            channel_id=target.channel_id,
                            topic_id=target.topic_id,
                            parser_version=version,
                            last_received_at=last_received_at,
                            last_raw_message_id=last_raw_message_id,
                        )
                    )
                else:
                    checkpoint.last_received_at = last_received_at
                    checkpoint.last_raw_message_id = last_raw_message_id

        results.append(
            SignalParsingResult(
                channel_id=target.channel_id,
                topic_id=target.topic_id,
                processed_count=processed_count,
                skipped_count=skipped_count,
            )
        )
    return results
