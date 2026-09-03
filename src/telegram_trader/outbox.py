from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.models import OutboxDeliveryReceipt, OutboxEvent


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
