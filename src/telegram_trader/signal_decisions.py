from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from telegram_trader.models import NormalizedSignal, SignalDecisionEvent, SignalDecisionRequest

DecisionAction = Literal["APPROVE", "REJECT"]
DecisionOutcome = Literal["APPROVED", "REJECTED", "REJECTED_STALE", "REJECTED_EXPIRED"]


def request_id_for_signal_row(signal_row_id: str) -> str:
    return hashlib.sha256(signal_row_id.encode()).hexdigest()


def _event_id(request_id: str, callback_query_id: str) -> str:
    return hashlib.sha256(f"{request_id}:{callback_query_id}".encode()).hexdigest()


def load_pending_signals(session: Session) -> list[NormalizedSignal]:
    """Latest revision per `signal_id`, `status IN ('NEW','VALIDATED')`, not yet requested.

    No channel filter is needed: `normalized_signals` only ever contains
    rows for `EXECUTION_SIGNAL` channels by construction of Slice 2's own
    target loading -- an `ANALYSIS` channel like bonnie-blockchain never
    produces a row here at all.
    """
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
        )
        .order_by(NormalizedSignal.created_at)
    )
    return list(session.scalars(stmt))


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

    session.execute(
        pg_insert(SignalDecisionEvent)
        .values(
            event_id=event_id,
            request_id=request_id,
            actor_user_id=actor_user_id,
            outcome=outcome,
            decided_at=moment,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    return outcome
