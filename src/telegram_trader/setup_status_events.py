"""Apply an analyst's status message (target hit / cancel / exit) to the setups it refers to.

User decisions, 2026-10-07:
- a setup still waiting for its entry is closed automatically;
- a draft the user has not decided on is voided (a CANCELLED revision, so Approve then shows as
  stale -- the same mechanism a cancelled Monster signal uses);
- a draft the user already approved (a position may be open) only produces a notification: the
  system never closes or reduces a position by itself;
- a message that names no coin and has no reply link is applied only when exactly one setup could
  be meant; otherwise the user is told it is ambiguous.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from telegram_trader.models import (
    AnalysisSetup,
    NormalizedContent,
    NormalizedSignal,
    OutboxEvent,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TelegramMessageVersion,
)
from telegram_trader.outbox import append_outbox_event
from telegram_trader.status_events import STATUS_LABELS, StatusKind, symbols_in_text

LOGGER = logging.getLogger(__name__)

_CANDIDATE_STATUSES = ("WATCHING", "ZONE_TOUCHED", "TRIGGERED")
_QUOTE_LENGTH = 100

Action = Literal["CLOSED", "DRAFT_VOIDED", "NOTIFIED", "AMBIGUOUS", "NO_TARGET"]


@dataclass(frozen=True, slots=True)
class StatusEventResult:
    action: Action
    setups_touched: int = 0


def _topic_condition(column: Any, topic_id: int | None) -> Any:
    return column.is_(None) if not topic_id else column == topic_id


def _event_id(subject: str, event_type: str, discriminator: str) -> str:
    return hashlib.sha256(f"{subject}:{event_type}:{discriminator}".encode()).hexdigest()


def _message_key(raw: TelegramMessageVersion) -> str:
    """One Telegram message, whatever its edit version: an edit that leaves the text unchanged
    (reaction/entity touch-ups) must not notify a second time; a real text change still does."""
    return f"{raw.channel_id}:{raw.message_id}:{raw.content_hash}"


def _emit(
    session: Session,
    *,
    subject: str,
    event_type: str,
    aggregate_type: str,
    discriminator: str,
    payload: dict[str, Any],
    dedupe_subject: str | None = None,
) -> None:
    """Idempotent: the same status message never notifies the same subject twice."""
    event_id = _event_id(dedupe_subject or subject, event_type, discriminator)
    if session.get(OutboxEvent, event_id) is not None:
        return
    append_outbox_event(
        session,
        event_id=event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=subject,
        payload=payload,
    )


def _candidates(
    session: Session, content: NormalizedContent, *, source_date: datetime, expiry_hours: int
) -> list[AnalysisSetup]:
    return list(
        session.scalars(
            select(AnalysisSetup).where(
                AnalysisSetup.channel_id == content.channel_id,
                _topic_condition(AnalysisSetup.topic_id, content.topic_id),
                AnalysisSetup.status.in_(_CANDIDATE_STATUSES),
                AnalysisSetup.valid_from <= source_date,
                AnalysisSetup.valid_from >= source_date - timedelta(hours=expiry_hours),
            )
        )
    )


def _pairs_from_reply(
    session: Session, content: NormalizedContent, raw: TelegramMessageVersion
) -> set[tuple[str, str]]:
    """`(symbol, side)` of the setups created from the message this one replies to."""
    if raw.reply_to_message_id is None:
        return set()
    replied = select(TelegramMessageVersion.source_event_id).where(
        TelegramMessageVersion.channel_id == content.channel_id,
        _topic_condition(TelegramMessageVersion.topic_id, content.topic_id),
        TelegramMessageVersion.message_id == raw.reply_to_message_id,
    )
    rows = session.execute(
        select(AnalysisSetup.symbol, AnalysisSetup.side).where(
            AnalysisSetup.raw_message_id.in_(replied)
        )
    ).all()
    return {(row[0], row[1]) for row in rows}


def _choose_pairs(
    candidates: list[AnalysisSetup],
    *,
    reply_pairs: set[tuple[str, str]],
    symbols: list[str],
) -> tuple[set[tuple[str, str]], bool]:
    """`(pairs to act on, determined)`: determined means the message named its target."""
    candidate_pairs = {(s.symbol, s.side) for s in candidates}
    if reply_pairs:
        pairs = candidate_pairs & reply_pairs
        if symbols:
            named = {pair for pair in pairs if pair[0] in symbols}
            pairs = named or pairs
        return pairs, True
    if symbols:
        return {pair for pair in candidate_pairs if pair[0] in symbols}, True
    return candidate_pairs, False


def _void_draft(
    session: Session, setup: AnalysisSetup, raw: TelegramMessageVersion
) -> NormalizedSignal | None:
    """A CANCELLED revision of the setup's draft, unless the user already decided on it.

    Returns the latest signal row when the draft is *already approved* (caller notifies only), or
    `None` when nothing needs notifying about a position (voided, rejected or no draft).
    """
    if setup.signal_id is None:
        return None
    latest = session.scalars(
        select(NormalizedSignal)
        .where(NormalizedSignal.signal_id == setup.signal_id)
        .order_by(NormalizedSignal.revision.desc())
        .limit(1)
    ).first()
    if latest is None or latest.status == "CANCELLED":
        return None
    request = session.scalars(
        select(SignalDecisionRequest).where(
            SignalDecisionRequest.signal_row_id == latest.signal_row_id
        )
    ).first()
    outcome = None
    if request is not None:
        outcome = session.scalars(
            select(SignalDecisionEvent.outcome).where(
                SignalDecisionEvent.request_id == request.request_id,
                SignalDecisionEvent.outcome.in_(("APPROVED", "REJECTED")),
            )
        ).first()
    if outcome == "APPROVED":
        return latest
    if outcome == "REJECTED":
        return None
    session.add(
        NormalizedSignal(
            signal_row_id=hashlib.sha256(
                f"{raw.source_event_id}:{latest.parser_version}".encode()
            ).hexdigest(),
            signal_id=latest.signal_id,
            revision=latest.revision + 1,
            raw_message_id=raw.source_event_id,
            normalizer_version=latest.normalizer_version,
            channel_id=latest.channel_id,
            topic_id=latest.topic_id,
            status="CANCELLED",
            symbol=latest.symbol,
            side=latest.side,
            entry_type=latest.entry_type,
            entry_values=latest.entry_values,
            stop_value=latest.stop_value,
            stop_origin=latest.stop_origin,
            take_profits=latest.take_profits,
            evidence_spans={},
            link_method="STATUS_MSG",
            parser_version=latest.parser_version,
            expires_at=latest.expires_at,
            suggested_size_pct=latest.suggested_size_pct,
        )
    )
    return None


def apply_status_event(
    session: Session,
    *,
    kind: StatusKind,
    content: NormalizedContent,
    raw: TelegramMessageVersion,
    source_date: datetime,
    valid_symbols: frozenset[str],
    now: datetime,
    expiry_hours: int,
    extra_symbol: str | None = None,
) -> StatusEventResult:
    symbols = symbols_in_text(content.normalized_text, valid_symbols)
    if extra_symbol and extra_symbol in valid_symbols and extra_symbol not in symbols:
        symbols.append(extra_symbol)
    candidates = _candidates(session, content, source_date=source_date, expiry_hours=expiry_hours)
    pairs, determined = _choose_pairs(
        candidates, reply_pairs=_pairs_from_reply(session, content, raw), symbols=symbols
    )
    quote = content.normalized_text.replace("\n", " ")[:_QUOTE_LENGTH]
    label = STATUS_LABELS[kind]

    if not pairs:
        return StatusEventResult("NO_TARGET")
    if len(pairs) > 1 or (not determined and len(pairs) != 1):
        _emit(
            session,
            subject=raw.source_event_id,
            event_type="setup_status_ambiguous",
            aggregate_type="normalized_content",
            discriminator=f"{kind}:{_message_key(raw)}",
            dedupe_subject=f"{raw.channel_id}",
            payload={
                "note": quote,
                "candidates": ", ".join(f"{symbol} {side}" for symbol, side in sorted(pairs)),
            },
        )
        return StatusEventResult("AMBIGUOUS")

    [(symbol, side)] = pairs
    action: Action = "NO_TARGET"
    touched = 0
    for setup in (s for s in candidates if (s.symbol, s.side) == (symbol, side)):
        reason = f"ANALYST_{kind}"
        base = {"symbol": symbol, "side": side, "reason": reason, "note": quote}
        if setup.status in ("WATCHING", "ZONE_TOUCHED"):
            setup.status = "INVALIDATED"
            setup.status_reason = reason
            setup.last_checked_at = now
            _emit(
                session,
                subject=setup.setup_id,
                event_type="setup_invalidated",
                aggregate_type="analysis_setup",
                discriminator=_message_key(raw),
                payload=base,
            )
            action, touched = "CLOSED", touched + 1
            continue
        approved = _void_draft(session, setup, raw)  # TRIGGERED
        setup.status_reason = reason
        if approved is not None:
            _emit(
                session,
                subject=setup.setup_id,
                event_type="setup_analyst_notice",
                aggregate_type="analysis_setup",
                discriminator=_message_key(raw),
                payload={
                    **base,
                    "note": f"{label}：{quote}",
                    "hint": "你已核准這筆；系統不會自動平倉或減倉，請自行決定",
                },
            )
            action, touched = "NOTIFIED", touched + 1
        else:
            _emit(
                session,
                subject=setup.setup_id,
                event_type="setup_draft_voided",
                aggregate_type="analysis_setup",
                discriminator=_message_key(raw),
                payload={**base, "note": f"{label}，這筆草稿已作廢：{quote}"},
            )
            action, touched = "DRAFT_VOIDED", touched + 1
    LOGGER.info(
        "analyst status event applied",
        extra={"context": {"kind": kind, "symbol": symbol, "action": action, "setups": touched}},
    )
    return StatusEventResult(action, touched)
