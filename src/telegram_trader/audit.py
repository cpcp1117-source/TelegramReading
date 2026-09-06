from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from telegram_trader.models import AuditEvent


def append_audit_event(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
) -> AuditEvent:
    event = AuditEvent(
        event_id=event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
    )
    session.add(event)
    return event
