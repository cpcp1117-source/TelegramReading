from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from telegram_trader.models import (
    NormalizedSignal,
    SignalDecisionEdit,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TelegramMessageVersion,
)

DecisionAction = Literal["APPROVE", "REJECT"]
DecisionOutcome = Literal["APPROVED", "REJECTED", "REJECTED_STALE", "REJECTED_EXPIRED"]
EditableField = Literal["STOP", "TAKE_PROFIT"]


@dataclass(frozen=True, slots=True)
class DraftValues:
    stop_value: Decimal | None
    take_profits: list[Decimal]


def request_id_for_signal_row(signal_row_id: str) -> str:
    return hashlib.sha256(signal_row_id.encode()).hexdigest()


def _event_id(request_id: str, callback_query_id: str) -> str:
    return hashlib.sha256(f"{request_id}:{callback_query_id}".encode()).hexdigest()


def load_pending_signals(
    session: Session, *, now: datetime | None = None
) -> list[NormalizedSignal]:
    """Latest revision per `signal_id`, `status IN ('NEW','VALIDATED')`, not yet
    requested, and not yet past its own `expires_at`.

    The `expires_at` check matters beyond live operation: a `parser_version`
    bump re-parses the entire real backlog and appends fresh rows for every
    message, including ones from weeks ago. Without this check, a signal
    whose underlying message is long past its 24h `expires_at` window would
    still look brand new here (never `signal_decision_requests`-requested
    under its new `parser_version`) and flood a stale, already-irrelevant
    notification the moment the version bump lands.

    No channel filter is needed: `normalized_signals` only ever contains
    rows for `EXECUTION_SIGNAL` channels by construction of Slice 2's own
    target loading -- an `ANALYSIS` channel like bonnie-blockchain never
    produces a row here at all.
    """
    moment = now if now is not None else datetime.now(UTC)
    latest_revision = (
        select(
            NormalizedSignal.signal_id,
            func.max(NormalizedSignal.revision).label("max_revision"),
        )
        .group_by(NormalizedSignal.signal_id)
        .subquery()
    )
    already_requested = select(SignalDecisionRequest.signal_row_id)
    stmt = (
        select(NormalizedSignal)
        .join(
            latest_revision,
            (NormalizedSignal.signal_id == latest_revision.c.signal_id)
            & (NormalizedSignal.revision == latest_revision.c.max_revision),
        )
        .where(
            NormalizedSignal.status.in_(("NEW", "VALIDATED")),
            NormalizedSignal.signal_row_id.not_in(already_requested),
            NormalizedSignal.expires_at > moment,
        )
        .order_by(NormalizedSignal.created_at)
    )
    return list(session.scalars(stmt))


def load_pending_signals_with_source(
    session: Session, *, now: datetime | None = None
) -> list[tuple[NormalizedSignal, TelegramMessageVersion]]:
    """Same rows as `load_pending_signals`, joined to the raw message's

    `source_date`/`received_at` -- needed by `trade_intent_pipeline.preview_trade_intent`
    for BR-006's receive-lag/price-deviation checks, the same join
    `trade_intent_pipeline.load_unprocessed_approved_events` already does
    post-approval. A dedicated function rather than widening
    `load_pending_signals` itself: `/status` and `/signals` only ever need
    the bare signal list, not this join.
    """
    moment = now if now is not None else datetime.now(UTC)
    latest_revision = (
        select(
            NormalizedSignal.signal_id,
            func.max(NormalizedSignal.revision).label("max_revision"),
        )
        .group_by(NormalizedSignal.signal_id)
        .subquery()
    )
    already_requested = select(SignalDecisionRequest.signal_row_id)
    stmt = (
        select(NormalizedSignal, TelegramMessageVersion)
        .join(
            latest_revision,
            (NormalizedSignal.signal_id == latest_revision.c.signal_id)
            & (NormalizedSignal.revision == latest_revision.c.max_revision),
        )
        .join(
            TelegramMessageVersion,
            NormalizedSignal.raw_message_id == TelegramMessageVersion.source_event_id,
        )
        .where(
            NormalizedSignal.status.in_(("NEW", "VALIDATED")),
            NormalizedSignal.signal_row_id.not_in(already_requested),
            NormalizedSignal.expires_at > moment,
        )
        .order_by(NormalizedSignal.created_at)
    )
    return list(session.execute(stmt).all())  # type: ignore[arg-type]


def create_request(
    session: Session,
    signal: NormalizedSignal,
    *,
    nonce: str,
    telegram_message_id: int,
) -> None:
    """Insert-only: called after the Telegram send already succeeded, so this

    row's `telegram_message_id` is always known up front and the row is
    never updated afterward.
    """
    session.add(
        SignalDecisionRequest(
            request_id=request_id_for_signal_row(signal.signal_row_id),
            signal_id=signal.signal_id,
            signal_row_id=signal.signal_row_id,
            channel_id=signal.channel_id,
            topic_id=signal.topic_id,
            nonce=nonce,
            telegram_message_id=telegram_message_id,
            expires_at=signal.expires_at,
        )
    )


def find_request_by_nonce(session: Session, nonce: str) -> SignalDecisionRequest | None:
    """Resolve a button tap's callback data back to its request.

    Telegram caps callback data at 64 bytes, too small to carry the full
    64-character `request_id` alongside the nonce -- the nonce (128 bits of
    randomness, `UNIQUE`-constrained) is the only value that needs to travel
    in the button itself. An unresolvable nonce (forged/garbage callback
    data) simply returns `None`, the same fail-closed outcome as an unknown
    `request_id` did before this lookup existed.
    """
    return session.scalar(select(SignalDecisionRequest).where(SignalDecisionRequest.nonce == nonce))


def load_current_draft(session: Session, request: SignalDecisionRequest) -> DraftValues:
    """The latest edit for this request, or the original signal's own values if never edited.

    `signal_decision_edits` stores a full snapshot per row (not a partial
    diff), so "current draft" is always just the latest row by revision --
    no merge logic needed here.
    """
    latest_edit = session.scalar(
        select(SignalDecisionEdit)
        .where(SignalDecisionEdit.request_id == request.request_id)
        .order_by(SignalDecisionEdit.revision.desc())
        .limit(1)
    )
    if latest_edit is not None:
        return DraftValues(
            stop_value=latest_edit.stop_value,
            take_profits=[Decimal(str(value)) for value in latest_edit.take_profits],
        )
    signal = session.get(NormalizedSignal, request.signal_row_id)
    if signal is None:
        return DraftValues(stop_value=None, take_profits=[])
    return DraftValues(
        stop_value=signal.stop_value,
        take_profits=[Decimal(str(value)) for value in signal.take_profits],
    )


def create_edit(
    session: Session,
    request: SignalDecisionRequest,
    *,
    field: EditableField,
    value: Decimal | list[Decimal],
) -> DraftValues:
    """Append one full-draft-snapshot edit row, carrying the untouched field forward.

    Editing `TAKE_PROFIT` replaces the whole take-profit list with the
    single new value the user typed -- matching the confirmed "reply with
    one new number" interaction, not a multi-value syntax.
    """
    current = load_current_draft(session, request)
    new_stop_value: Decimal | None
    new_take_profits: list[Decimal]
    if field == "STOP":
        assert isinstance(value, Decimal)
        new_stop_value = value
        new_take_profits = current.take_profits
    else:
        assert isinstance(value, list)
        new_stop_value = current.stop_value
        new_take_profits = value

    next_revision = (
        session.scalar(
            select(func.max(SignalDecisionEdit.revision)).where(
                SignalDecisionEdit.request_id == request.request_id
            )
        )
        or 0
    ) + 1
    session.add(
        SignalDecisionEdit(
            edit_id=hashlib.sha256(f"{request.request_id}:{next_revision}".encode()).hexdigest(),
            request_id=request.request_id,
            revision=next_revision,
            stop_value=new_stop_value,
            take_profits=[str(tp) for tp in new_take_profits],
        )
    )
    return DraftValues(stop_value=new_stop_value, take_profits=new_take_profits)


def _is_current_revision(session: Session, request: SignalDecisionRequest) -> bool:
    max_revision = session.scalar(
        select(func.max(NormalizedSignal.revision)).where(
            NormalizedSignal.signal_id == request.signal_id
        )
    )
    current_row = session.get(NormalizedSignal, request.signal_row_id)
    return current_row is not None and current_row.revision == max_revision


def record_decision(
    session: Session,
    *,
    request_id: str,
    actor_user_id: int,
    callback_query_id: str,
    action: DecisionAction,
    now: datetime | None = None,
) -> DecisionOutcome | None:
    """Record one decision attempt by the already-allowlist-verified actor.

    The caller (`control_bot.py`) is responsible for verifying the sender
    is the configured allowlisted user *before* calling this -- an
    unauthorized sender is rejected and logged there, and never reaches
    this function at all, so there is nothing to re-check here beyond the
    request's own state (Data Invariant #4: allowlisted actor, nonce,
    unexpired, current state).

    Returns `None` if `request_id` doesn't correspond to any known request
    (forged/garbage callback data); otherwise the recorded outcome.

    Idempotent against Telegram's at-least-once delivery of a button tap:
    if this exact `(request_id, callback_query_id)` was already recorded,
    the original outcome is returned unchanged rather than re-derived --
    otherwise a redelivered "APPROVE" tap could come back as
    `REJECTED_STALE` on its second delivery (a decision now exists) even
    though nothing new actually happened.
    """
    request = session.get(SignalDecisionRequest, request_id)
    if request is None:
        return None

    event_id = _event_id(request_id, callback_query_id)
    already_recorded = session.get(SignalDecisionEvent, event_id)
    if already_recorded is not None:
        return cast(DecisionOutcome, already_recorded.outcome)

    moment = now or datetime.now(UTC)

    existing_decision = session.scalar(
        select(SignalDecisionEvent).where(
            SignalDecisionEvent.request_id == request_id,
            SignalDecisionEvent.outcome.in_(("APPROVED", "REJECTED")),
        )
    )
    outcome: DecisionOutcome
    if existing_decision is not None:
        outcome = "REJECTED_STALE"
    elif moment > request.expires_at:
        outcome = "REJECTED_EXPIRED"
    elif not _is_current_revision(session, request):
        outcome = "REJECTED_STALE"
    else:
        outcome = "APPROVED" if action == "APPROVE" else "REJECTED"

    draft = load_current_draft(session, request)
    session.execute(
        pg_insert(SignalDecisionEvent)
        .values(
            event_id=event_id,
            request_id=request_id,
            actor_user_id=actor_user_id,
            outcome=outcome,
            approved_stop_value=draft.stop_value,
            approved_take_profits=[str(tp) for tp in draft.take_profits],
            decided_at=moment,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    return outcome
