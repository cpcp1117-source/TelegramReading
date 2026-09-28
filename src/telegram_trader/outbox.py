from __future__ import annotations

from dataclasses import dataclass
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
