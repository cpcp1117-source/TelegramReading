from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from telegram_trader import cli as cli_module
from telegram_trader.channel_policy import (
    ChannelPolicyError,
    evaluate_raw_collection,
    load_channel_policies,
    resolve_effective_targets,
)
from telegram_trader.config import Settings, TelegramChannelTarget, get_settings
from telegram_trader.db import create_db_engine, create_session_factory, database_is_ready
from telegram_trader.mock_telegram import (
    MockMessageProcessor,
    MockTelegramMessage,
    SequenceGapError,
)
from telegram_trader.models import (
    AuditEvent,
    ChannelPolicy,
    ConsumerCheckpoint,
    MockMessageReceipt,
    OutboxDeliveryReceipt,
    OutboxEvent,
    TelegramCollectorCheckpoint,
    TelegramMessageVersion,
)
from telegram_trader.outbox import OutboxConsumer
from telegram_trader.retention_cleanup import run_retention_cleanup
from telegram_trader.telegram_collector import TelethonReadOnlyCollector
from telegram_trader.telegram_storage import (
    MediaStore,
    TelegramMessageInput,
    TelegramMessageProcessor,
)

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
                "telegram_message_versions, telegram_collector_checkpoints, "
                "mock_message_receipts, consumer_checkpoints, audit_events, "
                "channel_policies "
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


def _telegram_input(**overrides: object) -> TelegramMessageInput:
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "channel_id": 2439599598,
        "message_id": 100,
        "event_kind": "NEW",
        "source_date": now,
        "received_at": now,
        "text": "synthetic telegram message",
        "content_type": "text",
    }
    values.update(overrides)
    return TelegramMessageInput(**values)  # type: ignore[arg-type]


def test_telegram_replay_and_edit_versions_are_append_only(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(2439599598, None)})
    )

    original = processor.process(_telegram_input(text="original"))
    replay = processor.process(_telegram_input(text="original", event_kind="BACKFILL"))
    edited = processor.process(
        _telegram_input(text="edited", event_kind="EDITED", edit_date=datetime.now(UTC))
    )

    assert original.edit_version == 0
    assert replay.duplicate is True
    assert replay.source_event_id == original.source_event_id
    assert edited.edit_version == 1
    assert edited.duplicate is False
    with factory() as session:
        versions = list(
            session.scalars(
                select(TelegramMessageVersion).order_by(TelegramMessageVersion.edit_version)
            )
        )
        assert [version.text for version in versions] == ["original", "edited"]
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2


def test_telegram_relationships_media_and_checkpoint_survive_restart(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    first = TelegramMessageProcessor(
        factory, MediaStore(media_root), frozenset({(2439599598, None)})
    )
    image = b"synthetic-image"

    result = first.process(
        _telegram_input(
            message_id=101,
            content_type="image",
            reply_to_message_id=99,
            forward_origin_type="channel",
            forward_origin_id=123,
            forward_message_id=456,
            forward_date=datetime.now(UTC),
            media_bytes=image,
            media_filename="chart.jpg",
            media_mime_type="image/jpeg",
        )
    )

    restarted = TelegramMessageProcessor(
        factory, MediaStore(media_root), frozenset({(2439599598, None)})
    )
    assert restarted.checkpoint(2439599598) == 101
    assert result.media_sha256 == hashlib.sha256(image).hexdigest()
    with factory() as session:
        version = session.scalar(select(TelegramMessageVersion))
        checkpoint = session.get(TelegramCollectorCheckpoint, (2439599598, 0))
        assert version is not None
        assert checkpoint is not None
        assert version.reply_to_message_id == 99
        assert version.forward_origin_type == "channel"
        assert version.forward_origin_id == 123
        assert version.forward_message_id == 456
        assert version.media_path is not None
        assert (media_root / version.media_path).read_bytes() == image
        assert checkpoint.last_message_id == 101


def test_telegram_message_version_cannot_be_mutated(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(2439599598, None)})
    )
    processor.process(_telegram_input())

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE telegram_message_versions SET text='changed'"))

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("DELETE FROM telegram_message_versions"))


def test_telegram_checkpoints_are_isolated_per_topic(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory,
        MediaStore(tmp_path / "media"),
        frozenset({(2382278102, 21), (2382278102, 22)}),
    )

    processor.process(_telegram_input(channel_id=2382278102, topic_id=21, message_id=50))
    processor.process(_telegram_input(channel_id=2382278102, topic_id=22, message_id=999))

    assert processor.checkpoint(2382278102, 21) == 50
    assert processor.checkpoint(2382278102, 22) == 999


def test_telegram_processor_rejects_unlisted_target(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(2439599598, None)})
    )

    with pytest.raises(ValueError, match="not an allowlisted"):
        processor.process(_telegram_input(channel_id=2382278102, topic_id=21))


def _channel_policy(**overrides: object) -> ChannelPolicy:
    values: dict[str, object] = {
        "channel_id": 1,
        "topic_id": 0,
        "label": "Test Channel",
        "channel_type": "ANALYSIS",
        "symbol_scope_mode": "STATIC_ALLOWLIST",
        "gate_decision": "MONITOR_ONLY",
    }
    values.update(overrides)
    return ChannelPolicy(**values)


def test_channel_policy_round_trip_including_jsonb_fields(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=1,
                topic_id=0,
                allowed_symbols=["BTCUSDT", "ETHUSDT"],
                policy_detail={"thesis_structure": "resistance then support"},
            )
        )

    with factory() as session:
        row = session.get(ChannelPolicy, (1, 0))
        assert row is not None
        assert row.allowed_symbols == ["BTCUSDT", "ETHUSDT"]
        assert row.policy_detail == {"thesis_structure": "resistance then support"}
        assert row.automation_authorization == "UNKNOWN"
        assert row.raw_retention_days == 7


def test_channel_policy_rejects_invalid_channel_type(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with (
        pytest.raises(IntegrityError, match="ck_channel_policy_type_valid"),
        factory.begin() as session,
    ):
        session.add(_channel_policy(channel_type="NOT_A_TYPE"))


def test_channel_policy_rejects_invalid_authorization_value(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with (
        pytest.raises(IntegrityError, match="ck_channel_policy_automation_auth_valid"),
        factory.begin() as session,
    ):
        session.add(_channel_policy(automation_authorization="MAYBE"))


def test_channel_policy_rejects_invalid_gate_decision(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with (
        pytest.raises(IntegrityError, match="ck_channel_policy_gate_decision_valid"),
        factory.begin() as session,
    ):
        session.add(_channel_policy(gate_decision="SOMETIMES"))


def test_seeded_real_channel_values_evaluate_as_authorized(engine: Engine) -> None:
    """Regression guard: today's declared markdown values must still gate to allowed=True."""
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=2439599598,
                topic_id=0,
                label="Monster-貨幣宇宙中心",
                username="followgerry",
                channel_type="EXECUTION_SIGNAL",
                access_authorization="GRANTED",
                automation_authorization="GRANTED",
                ai_authorization="GRANTED",
                media_authorization="GRANTED",
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                gate_decision="MONITOR_ONLY",
            )
        )
        session.add(
            _channel_policy(
                channel_id=2382278102,
                topic_id=21,
                label="邦妮區塊鏈-BTC ETH 即時更新",
                channel_type="ANALYSIS",
                access_authorization="GRANTED",
                automation_authorization="GRANTED",
                ai_authorization="GRANTED",
                media_authorization="GRANTED",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT", "ETHUSDT"],
                gate_decision="MONITOR_ONLY",
            )
        )

    targets = [
        TelegramChannelTarget(
            channel_id=2439599598, topic_id=None, username="followgerry", label="A"
        ),
        TelegramChannelTarget(channel_id=2382278102, topic_id=21, username=None, label="B"),
    ]
    with factory() as session:
        policies = load_channel_policies(session, targets)

    assert evaluate_raw_collection(policies[(2439599598, 0)]).allowed is True
    assert evaluate_raw_collection(policies[(2382278102, 21)]).allowed is True


def test_load_channel_policies_returns_none_for_unregistered_target(engine: Engine) -> None:
    factory = create_session_factory(engine)
    targets = [TelegramChannelTarget(channel_id=999, topic_id=None, username=None, label="X")]

    with factory() as session:
        policies = load_channel_policies(session, targets)

    assert policies == {}
    assert evaluate_raw_collection(policies.get((999, 0))).allowed is False


def test_resolve_effective_targets_returns_only_authorized(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=1,
                topic_id=0,
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
        session.add(
            _channel_policy(
                channel_id=2,
                topic_id=0,
                automation_authorization="GRANTED",
                gate_decision="PAUSED",
            )
        )
    targets = [
        TelegramChannelTarget(channel_id=1, topic_id=None, username=None, label="Allowed"),
        TelegramChannelTarget(channel_id=2, topic_id=None, username=None, label="Paused"),
    ]

    with factory() as session:
        effective = resolve_effective_targets(session, targets, logging.getLogger("test"))

    assert [target.label for target in effective] == ["Allowed"]


def test_resolve_effective_targets_fails_closed_when_none_authorized(engine: Engine) -> None:
    factory = create_session_factory(engine)
    targets = [TelegramChannelTarget(channel_id=999, topic_id=None, username=None, label="X")]

    with (
        pytest.raises(ChannelPolicyError, match="no configured Telegram target is authorized"),
        factory() as session,
    ):
        resolve_effective_targets(session, targets, logging.getLogger("test"))


def test_retention_cleanup_deletes_only_rows_past_declared_retention(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    processor = TelegramMessageProcessor(factory, MediaStore(media_root), frozenset({(555, None)}))
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=555, topic_id=0, raw_retention_days=1))

    old_result = processor.process(
        _telegram_input(
            channel_id=555,
            message_id=1,
            received_at=now - timedelta(days=2),
            content_type="image",
            media_bytes=b"old-chart",
            media_filename="old.png",
        )
    )
    processor.process(
        _telegram_input(
            channel_id=555,
            message_id=2,
            received_at=now,
            content_type="image",
            media_bytes=b"new-chart",
            media_filename="new.png",
        )
    )

    results = run_retention_cleanup(factory, MediaStore(media_root), now=lambda: now)

    assert len(results) == 1
    assert results[0].deleted_row_count == 1
    assert results[0].deleted_media_count == 1
    with factory() as session:
        remaining = list(session.scalars(select(TelegramMessageVersion)))
        assert [version.message_id for version in remaining] == [2]
        checkpoint = session.get(TelegramCollectorCheckpoint, (555, 0))
        assert checkpoint is not None
        assert checkpoint.last_message_id == 2
    assert old_result.media_sha256 is not None
    assert not (media_root / "555" / "1").exists()
    assert (media_root / "555" / "2").exists()


def test_retention_cleanup_respects_topic_scope(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    processor = TelegramMessageProcessor(
        factory, MediaStore(media_root), frozenset({(556, None), (556, 21)})
    )
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    old = now - timedelta(days=10)
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=556, topic_id=0, raw_retention_days=1))
        session.add(_channel_policy(channel_id=556, topic_id=21, raw_retention_days=100))

    processor.process(_telegram_input(channel_id=556, topic_id=None, message_id=1, received_at=old))
    processor.process(_telegram_input(channel_id=556, topic_id=21, message_id=2, received_at=old))

    run_retention_cleanup(factory, MediaStore(media_root), now=lambda: now)

    with factory() as session:
        remaining = {
            version.topic_id for version in session.scalars(select(TelegramMessageVersion))
        }
        assert remaining == {21}


def test_retention_cleanup_processes_all_rows_across_batches(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    processor = TelegramMessageProcessor(factory, MediaStore(media_root), frozenset({(557, None)}))
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=557, topic_id=0, raw_retention_days=1))
    for message_id in range(1, 4):
        processor.process(
            _telegram_input(
                channel_id=557, message_id=message_id, received_at=now - timedelta(days=5)
            )
        )

    results = run_retention_cleanup(factory, MediaStore(media_root), now=lambda: now, batch_size=1)

    assert results[0].deleted_row_count == 3
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(TelegramMessageVersion)) == 0


def test_retention_trigger_rejects_delete_of_non_expired_row(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    processor = TelegramMessageProcessor(factory, MediaStore(media_root), frozenset({(558, None)}))
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=558, topic_id=0, raw_retention_days=100))
    processor.process(_telegram_input(channel_id=558, message_id=1, received_at=now))

    with (
        engine.connect() as connection,
        pytest.raises(DBAPIError, match="append-only"),
        connection.begin(),
    ):
        connection.execute(text("SET LOCAL app.retention_cleanup = 'on'"))
        connection.execute(text("DELETE FROM telegram_message_versions WHERE channel_id = 558"))


def test_retention_trigger_allows_delete_of_expired_row_with_guc_set(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    processor = TelegramMessageProcessor(factory, MediaStore(media_root), frozenset({(559, None)}))
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=559, topic_id=0, raw_retention_days=1))
    processor.process(
        _telegram_input(channel_id=559, message_id=1, received_at=now - timedelta(days=5))
    )

    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET LOCAL app.retention_cleanup = 'on'"))
        connection.execute(text("DELETE FROM telegram_message_versions WHERE channel_id = 559"))

    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramMessageVersion)
                .where(TelegramMessageVersion.channel_id == 559)
            )
            == 0
        )


@pytest.mark.anyio
async def test_reload_policy_once_removes_target_when_paused(engine: Engine) -> None:
    factory = create_session_factory(engine)
    target = TelegramChannelTarget(channel_id=560, topic_id=None, username=None, label="Live")
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=560,
                topic_id=0,
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    collector = TelethonReadOnlyCollector(object(), object(), targets=[target])  # type: ignore[arg-type]

    with factory.begin() as session:
        session.execute(
            text("UPDATE channel_policies SET gate_decision = 'PAUSED' WHERE channel_id = 560")
        )

    removed = await collector.reload_policy_once(factory, [target])

    assert [t.label for t in removed] == ["Live"]
    assert collector._matching_target(560, None) is None


@pytest.mark.anyio
async def test_reload_policy_once_leaves_enabled_target_unaffected(engine: Engine) -> None:
    factory = create_session_factory(engine)
    target = TelegramChannelTarget(channel_id=561, topic_id=None, username=None, label="Live")
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=561,
                topic_id=0,
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    collector = TelethonReadOnlyCollector(object(), object(), targets=[target])  # type: ignore[arg-type]

    removed = await collector.reload_policy_once(factory, [target])

    assert removed == []
    assert collector._matching_target(561, None) is target
