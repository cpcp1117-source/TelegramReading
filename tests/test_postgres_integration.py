from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from telegram_trader import cli as cli_module
from telegram_trader.config import Settings, get_settings
from telegram_trader.db import create_db_engine, create_session_factory, database_is_ready
from telegram_trader.mock_telegram import (
    MockMessageProcessor,
    MockTelegramMessage,
    SequenceGapError,
)
from telegram_trader.models import (
    AuditEvent,
    ConsumerCheckpoint,
    MockMessageReceipt,
    OutboxDeliveryReceipt,
    OutboxEvent,
)
from telegram_trader.outbox import OutboxConsumer

pytestmark = pytest.mark.integration


@pytest.fixture
def engine() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    integration_settings = Settings(database_url=database_url) if database_url else Settings()
    active_engine = create_db_engine(integration_settings)
    with active_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE outbox_delivery_receipts, outbox_events, "
                "mock_message_receipts, consumer_checkpoints, audit_events "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield active_engine
    active_engine.dispose()


def test_replay_is_idempotent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    processor = MockMessageProcessor(factory, f"test-{uuid.uuid4()}")
    message = MockTelegramMessage("mock", 1, 0, 1, "synthetic")

    first = processor.process(message)
    second = processor.process(message)

    assert first.duplicate is False
    assert second.duplicate is True
    assert first.source_event_id == second.source_event_id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert session.scalar(select(func.count()).select_from(MockMessageReceipt)) == 1


def test_database_readiness(engine: Engine) -> None:
    assert database_is_ready(engine) is True


def test_checkpoint_survives_new_processor_instance(engine: Engine) -> None:
    factory = create_session_factory(engine)
    consumer = f"restart-{uuid.uuid4()}"
    first_process = MockMessageProcessor(factory, consumer)
    first_process.process(MockTelegramMessage("mock", 1, 0, 1, "before restart"))

    restarted_process = MockMessageProcessor(factory, consumer)
    assert restarted_process.checkpoint() == 1
    result = restarted_process.process(MockTelegramMessage("mock", 2, 0, 2, "after restart"))
    assert result.checkpoint == 2


def test_sequence_gap_fails_closed_without_advancing_checkpoint(engine: Engine) -> None:
    factory = create_session_factory(engine)
    consumer = f"gap-{uuid.uuid4()}"
    processor = MockMessageProcessor(factory, consumer)

    with pytest.raises(SequenceGapError, match="expected sequence 1"):
        processor.process(MockTelegramMessage("mock", 2, 0, 2, "gap"))
    assert processor.checkpoint() == 0


def test_crash_before_commit_rolls_back_audit_outbox_receipt_and_checkpoint(
    engine: Engine,
) -> None:
    factory = create_session_factory(engine)
    processor = MockMessageProcessor(factory, f"crash-before-{uuid.uuid4()}")

    def fail_before_commit(_session: Session) -> None:
        raise RuntimeError("simulated crash before commit")

    event.listen(factory.class_, "before_commit", fail_before_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated crash before commit"):
            processor.process(MockTelegramMessage("mock", 1, 0, 1, "not committed"))
    finally:
        event.remove(factory.class_, "before_commit", fail_before_commit)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.scalar(select(func.count()).select_from(MockMessageReceipt)) == 0
        assert session.scalar(select(func.count()).select_from(ConsumerCheckpoint)) == 0


def test_crash_after_commit_replay_is_a_no_op(engine: Engine) -> None:
    factory = create_session_factory(engine)
    consumer = f"crash-after-{uuid.uuid4()}"
    message = MockTelegramMessage("mock", 1, 0, 1, "committed")
    first = MockMessageProcessor(factory, consumer).process(message)

    replay = MockMessageProcessor(factory, consumer).process(message)

    assert replay.duplicate is True
    assert replay.outbox_event_id == first.outbox_event_id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert session.scalar(select(func.count()).select_from(MockMessageReceipt)) == 1


def test_outbox_delivery_is_idempotent_per_consumer(engine: Engine) -> None:
    factory = create_session_factory(engine)
    produced = MockMessageProcessor(factory, f"producer-{uuid.uuid4()}").process(
        MockTelegramMessage("mock", 1, 0, 1, "deliver once")
    )
    first_consumer = OutboxConsumer(factory, f"consumer-a-{uuid.uuid4()}")
    second_consumer = OutboxConsumer(factory, f"consumer-b-{uuid.uuid4()}")

    assert first_consumer.acknowledge(produced.outbox_event_id).duplicate is False
    assert first_consumer.acknowledge(produced.outbox_event_id).duplicate is True
    assert second_consumer.acknowledge(produced.outbox_event_id).duplicate is False
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(OutboxDeliveryReceipt)) == 2


def test_database_constraint_rejects_negative_checkpoint(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with (
        pytest.raises(IntegrityError, match="ck_checkpoint_non_negative"),
        factory.begin() as session,
    ):
        session.add(ConsumerCheckpoint(consumer_name="invalid", last_sequence=-1))


def test_audit_event_cannot_be_updated_or_deleted(engine: Engine) -> None:
    factory = create_session_factory(engine)
    consumer = f"audit-{uuid.uuid4()}"
    processor = MockMessageProcessor(factory, consumer)
    result = processor.process(MockTelegramMessage("mock", 1, 0, 1, "immutable"))

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text("UPDATE audit_events SET event_type='changed' WHERE event_id=:event_id"),
            {"event_id": result.audit_event_id},
        )

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text("DELETE FROM audit_events WHERE event_id=:event_id"),
            {"event_id": result.audit_event_id},
        )

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text("UPDATE outbox_events SET event_type='changed' WHERE event_id=:event_id"),
            {"event_id": result.outbox_event_id},
        )

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text("DELETE FROM outbox_events WHERE event_id=:event_id"),
            {"event_id": result.outbox_event_id},
        )

    with factory() as session:
        checkpoint = session.get(ConsumerCheckpoint, consumer)
        assert checkpoint is not None
        assert checkpoint.last_sequence == 1


def test_cli_emit_and_inspect(engine: Engine, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    consumer = f"cli-{uuid.uuid4()}"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "telegram-trader",
            "emit",
            "--consumer",
            consumer,
            "--message-id",
            "1",
            "--sequence",
            "1",
            "--text",
            "synthetic cli event",
        ],
    )
    assert cli_module.main() == 0
    assert '"duplicate": false' in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["telegram-trader", "inspect", "--consumer", consumer])
    assert cli_module.main() == 0
    output = capsys.readouterr().out
    assert '"checkpoint": 1' in output
    assert '"receipt_count": 1' in output
    assert '"outbox_count": 1' in output
    get_settings.cache_clear()


def test_cli_simulates_fixture(engine: Engine, monkeypatch, capsys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    consumer = f"fixture-{uuid.uuid4()}"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        '[{"channel_id":"mock","message_id":1,"edit_version":0,"sequence":1,"text":"fixture"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["telegram-trader", "simulate", "--consumer", consumer, "--fixture", str(fixture)],
    )
    assert cli_module.main() == 0
    output = capsys.readouterr().out
    assert '"checkpoint": 1' in output
    assert '"duplicate": false' in output
    get_settings.cache_clear()
