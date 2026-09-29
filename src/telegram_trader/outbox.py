from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.models import OutboxDeliveryReceipt, OutboxEvent


def load_pending_events(
    session: Session, *, consumer_name: str, event_type_prefix: str | None = None
) -> list[OutboxEvent]:
    """Every `OutboxEvent` this `consumer_name` has not yet acknowledged, oldest first.

    A generic, per-consumer "at least once" read -- the same event can be
    read by multiple distinct consumers independently (each has its own
    delivery-receipt row), and re-reading before acknowledging is always
    safe (idempotent by design, matching `OutboxConsumer.acknowledge`).
    """
    already_delivered = select(OutboxDeliveryReceipt.event_id).where(
        OutboxDeliveryReceipt.consumer_name == consumer_name
    )
    stmt = select(OutboxEvent).where(OutboxEvent.event_id.not_in(already_delivered))
    if event_type_prefix is not None:
        stmt = stmt.where(OutboxEvent.event_type.startswith(event_type_prefix))
    stmt = stmt.order_by(OutboxEvent.recorded_at)
    return list(session.scalars(stmt))


def load_events_due_for_reminder(
    session: Session,
    *,
    event_types: Collection[str],
    delivered_consumer: str,
    handled_consumer: str,
    reminded_consumer: str,
    delivered_before: datetime,
) -> list[OutboxEvent]:
    """Events of `event_types` that `delivered_consumer` delivered at or before
    `delivered_before`, and that neither `handled_consumer` nor
    `reminded_consumer` has a receipt for yet, oldest first.
    """
    delivered = select(OutboxDeliveryReceipt.event_id).where(
        OutboxDeliveryReceipt.consumer_name == delivered_consumer,
        OutboxDeliveryReceipt.delivered_at <= delivered_before,
    )
    settled = select(OutboxDeliveryReceipt.event_id).where(
        OutboxDeliveryReceipt.consumer_name.in_((handled_consumer, reminded_consumer))
    )
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.event_type.in_(tuple(event_types)),
            OutboxEvent.event_id.in_(delivered),
            OutboxEvent.event_id.not_in(settled),
        )
        .order_by(OutboxEvent.recorded_at)
    )
    return list(session.scalars(stmt))


def find_event_by_id_prefix(session: Session, prefix: str) -> OutboxEvent | None:
    """Resolves a Telegram button's truncated event id (callback data is capped at
    64 bytes, too small for the full 64-char id). `None` if absent or ambiguous.
    """
    if not prefix or not all(char in "0123456789abcdef" for char in prefix):
        return None
    matches = list(
        session.scalars(select(OutboxEvent).where(OutboxEvent.event_id.startswith(prefix)).limit(2))
    )
    return matches[0] if len(matches) == 1 else None


def append_outbox_event(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    event = OutboxEvent(
        event_id=event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
    )
    session.add(event)
    return event


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    event_id: str
    consumer_name: str
    duplicate: bool


class OutboxConsumer:
    def __init__(self, session_factory: sessionmaker[Session], consumer_name: str) -> None:
        if not consumer_name.strip():
            raise ValueError("consumer_name is required")
        self._session_factory = session_factory
        self._consumer_name = consumer_name

    def acknowledge(self, event_id: str) -> DeliveryResult:
        if not event_id.strip():
            raise ValueError("event_id is required")
        with self._session_factory.begin() as session:
            if session.get(OutboxEvent, event_id) is None:
                raise LookupError(f"unknown outbox event: {event_id}")
            identity = (self._consumer_name, event_id)
            if session.get(OutboxDeliveryReceipt, identity) is not None:
                return DeliveryResult(event_id, self._consumer_name, duplicate=True)
            session.add(OutboxDeliveryReceipt(consumer_name=self._consumer_name, event_id=event_id))
        return DeliveryResult(event_id, self._consumer_name, duplicate=False)
