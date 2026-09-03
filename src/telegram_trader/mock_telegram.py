from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.audit import append_audit_event
from telegram_trader.models import ConsumerCheckpoint, MockMessageReceipt


class SequenceGapError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MockTelegramMessage:
    channel_id: str
    message_id: int
    edit_version: int
    sequence: int
    text: str

    def __post_init__(self) -> None:
        if not self.channel_id.strip():
            raise ValueError("channel_id is required")
        if self.message_id <= 0:
            raise ValueError("message_id must be positive")
        if self.edit_version < 0:
            raise ValueError("edit_version must be non-negative")
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")

    @property
    def source_event_id(self) -> str:
        identity = f"{self.channel_id}:{self.message_id}:{self.edit_version}"
        return hashlib.sha256(identity.encode()).hexdigest()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    def audit_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    source_event_id: str
    audit_event_id: str
    duplicate: bool
    checkpoint: int


class MockMessageProcessor:
    def __init__(self, session_factory: sessionmaker[Session], consumer_name: str) -> None:
        if not consumer_name.strip():
            raise ValueError("consumer_name is required")
        self._session_factory = session_factory
        self._consumer_name = consumer_name

    def process(self, message: MockTelegramMessage) -> ProcessResult:
        audit_event_id = hashlib.sha256(
            f"mock-message-received:{message.source_event_id}".encode()
        ).hexdigest()
        with self._session_factory.begin() as session:
            existing = session.get(MockMessageReceipt, message.source_event_id)
            if existing is not None:
                checkpoint = self._checkpoint_value(session)
                return ProcessResult(
                    source_event_id=existing.source_event_id,
                    audit_event_id=existing.audit_event_id,
                    duplicate=True,
                    checkpoint=checkpoint,
                )

            checkpoint_row = session.execute(
                select(ConsumerCheckpoint)
                .where(ConsumerCheckpoint.consumer_name == self._consumer_name)
                .with_for_update()
            ).scalar_one_or_none()
            last_sequence = checkpoint_row.last_sequence if checkpoint_row is not None else 0
            expected = last_sequence + 1
            if message.sequence != expected:
                raise SequenceGapError(
                    f"expected sequence {expected}, received {message.sequence}; fail closed"
                )

            append_audit_event(
                session,
                event_id=audit_event_id,
                event_type="mock.telegram.message.received.v1",
                aggregate_type="mock_message",
                aggregate_id=message.source_event_id,
                payload=message.audit_payload(),
            )
            session.add(
                MockMessageReceipt(
                    source_event_id=message.source_event_id,
                    consumer_name=self._consumer_name,
                    channel_id=message.channel_id,
                    message_id=message.message_id,
                    edit_version=message.edit_version,
                    sequence_no=message.sequence,
                    content_hash=message.content_hash,
                    audit_event_id=audit_event_id,
                )
            )
            if checkpoint_row is None:
                checkpoint_row = ConsumerCheckpoint(
                    consumer_name=self._consumer_name,
                    last_sequence=message.sequence,
                )
                session.add(checkpoint_row)
            else:
                checkpoint_row.last_sequence = message.sequence

        return ProcessResult(
            source_event_id=message.source_event_id,
            audit_event_id=audit_event_id,
            duplicate=False,
            checkpoint=message.sequence,
        )

    def checkpoint(self) -> int:
        with self._session_factory() as session:
            return self._checkpoint_value(session)

    def _checkpoint_value(self, session: Session) -> int:
        checkpoint = session.get(ConsumerCheckpoint, self._consumer_name)
        return checkpoint.last_sequence if checkpoint is not None else 0


def load_fixture(raw_json: str) -> list[MockTelegramMessage]:
    value = json.loads(raw_json)
    if not isinstance(value, list):
        raise ValueError("fixture must be a JSON array")
    return [MockTelegramMessage(**item) for item in value]
