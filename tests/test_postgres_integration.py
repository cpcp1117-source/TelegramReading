from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from telegram_trader import cli as cli_module
from telegram_trader.binance_market_data import (
    compute_snapshot_id,
    load_latest_snapshot,
    refresh_snapshot,
)
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
    BinanceSymbolSnapshot,
    ChannelPolicy,
    ConsumerCheckpoint,
    MockMessageReceipt,
    OutboxDeliveryReceipt,
    OutboxEvent,
    SignalDecisionEdit,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TelegramCollectorCheckpoint,
    TelegramMessageVersion,
)
from telegram_trader.models import NormalizedContent as NormalizedContentRow
from telegram_trader.models import NormalizedSignal as NormalizedSignalRow
from telegram_trader.models import Thesis as ThesisRow
from telegram_trader.normalize_content import NormalizationResult, run_normalization
from telegram_trader.openai_client import OpenAiClientError
from telegram_trader.outbox import OutboxConsumer
from telegram_trader.parse_signals import SignalParsingResult, run_signal_parsing
from telegram_trader.retention_cleanup import run_retention_cleanup
from telegram_trader.signal_decisions import (
    create_edit,
    create_request,
    load_current_draft,
    load_pending_signals,
    record_decision,
    request_id_for_signal_row,
)
from telegram_trader.telegram_collector import TelethonReadOnlyCollector
from telegram_trader.telegram_storage import (
    MediaStore,
    TelegramMessageInput,
    TelegramMessageProcessor,
)
from telegram_trader.thesis_extraction import ThesisExtractionResult, run_thesis_extraction

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
                "signal_decision_edits, signal_decision_events, signal_decision_requests, "
                "normalized_signals, signal_parse_checkpoints, "
                "thesis, thesis_extraction_checkpoints, "
                "normalized_content, binance_symbol_snapshots, "
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


def test_run_normalization_resolves_static_allowlist_symbols(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(600, None)})
    )
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=600,
                topic_id=0,
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    processor.process(
        _telegram_input(channel_id=600, message_id=1, text="Long BTCUSDT, avoid DOGEUSDT")
    )

    results = run_normalization(factory)

    assert results == [NormalizationResult(channel_id=600, topic_id=0, processed_count=1)]
    with factory() as session:
        row = session.scalar(select(NormalizedContentRow))
        assert row is not None
        assert row.symbol_scope_mode == "STATIC_ALLOWLIST"
        assert sorted(row.symbol_candidates) == ["BTCUSDT", "DOGEUSDT"]
        resolved = {item["symbol"]: item["status"] for item in row.resolved_symbols}
        assert resolved == {"BTCUSDT": "VALID", "DOGEUSDT": "INVALID"}
        assert row.media_review_status == "NOT_APPLICABLE"


def test_run_normalization_marks_binance_dynamic_scope_pending(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(601, None)})
    )
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=601,
                topic_id=0,
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
    processor.process(_telegram_input(channel_id=601, message_id=1, text="Long BTCUSDT now"))

    run_normalization(factory)

    with factory() as session:
        row = session.scalar(select(NormalizedContentRow))
        assert row is not None
        resolved = {item["symbol"]: item["status"] for item in row.resolved_symbols}
        assert resolved == {"BTCUSDT": "PENDING_MARKET_DATA"}


def test_run_normalization_resolves_binance_dynamic_scope_with_fresh_snapshot(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(606, None)})
    )
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=606,
                topic_id=0,
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
        session.add(
            BinanceSymbolSnapshot(
                snapshot_id=compute_snapshot_id(
                    datetime(2026, 9, 13, 0, 0, tzinfo=UTC), frozenset({"BTCUSDT"})
                ),
                fetched_at=datetime(2026, 9, 13, 0, 0, tzinfo=UTC),
                active_symbols=["BTCUSDT"],
                symbol_count=1,
            )
        )
    processor.process(_telegram_input(channel_id=606, message_id=1, text="Long BTC now"))

    run_normalization(
        factory,
        snapshot_max_age_seconds=999_999_999.0,
    )

    with factory() as session:
        row = session.scalar(select(NormalizedContentRow))
        assert row is not None
        resolved = {item["symbol"]: item["status"] for item in row.resolved_symbols}
        assert resolved == {"BTCUSDT": "VALID"}


def test_run_normalization_stale_snapshot_still_pending_market_data(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(607, None)})
    )
    stale_fetched_at = datetime.now(UTC) - timedelta(days=2)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=607,
                topic_id=0,
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
        session.add(
            BinanceSymbolSnapshot(
                snapshot_id=compute_snapshot_id(stale_fetched_at, frozenset({"BTCUSDT"})),
                fetched_at=stale_fetched_at,
                active_symbols=["BTCUSDT"],
                symbol_count=1,
            )
        )
    processor.process(_telegram_input(channel_id=607, message_id=1, text="Long BTC now"))

    run_normalization(factory, snapshot_max_age_seconds=21600.0)

    with factory() as session:
        row = session.scalar(select(NormalizedContentRow))
        assert row is not None
        resolved = {item["symbol"]: item["status"] for item in row.resolved_symbols}
        assert resolved == {"BTC": "PENDING_MARKET_DATA"}


def test_binance_symbol_snapshots_cannot_be_updated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            BinanceSymbolSnapshot(
                snapshot_id=compute_snapshot_id(
                    datetime(2026, 9, 13, 0, 0, tzinfo=UTC), frozenset({"BTCUSDT"})
                ),
                fetched_at=datetime(2026, 9, 13, 0, 0, tzinfo=UTC),
                active_symbols=["BTCUSDT"],
                symbol_count=1,
            )
        )

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE binance_symbol_snapshots SET symbol_count=2"))


def test_refresh_snapshot_and_load_latest_snapshot_round_trip(engine: Engine) -> None:
    factory = create_session_factory(engine)
    fixed_now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    class _FakeMarketDataClient:
        def get_exchange_info(self) -> dict[str, object]:
            return {
                "symbols": [
                    {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
                    {"symbol": "ETHUSDT", "status": "BREAK", "contractType": "PERPETUAL"},
                ]
            }

    result = refresh_snapshot(_FakeMarketDataClient(), factory, clock=lambda: fixed_now)

    assert result.symbol_count == 1
    with factory() as session:
        loaded = load_latest_snapshot(session, clock=lambda: fixed_now, max_age_seconds=60.0)
        assert loaded is not None
        assert loaded.active_symbols == frozenset({"BTCUSDT"})
        assert loaded.fetched_at == fixed_now


def test_load_latest_snapshot_returns_none_when_stale(engine: Engine) -> None:
    factory = create_session_factory(engine)
    stale_fetched_at = datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(
            BinanceSymbolSnapshot(
                snapshot_id=compute_snapshot_id(stale_fetched_at, frozenset({"BTCUSDT"})),
                fetched_at=stale_fetched_at,
                active_symbols=["BTCUSDT"],
                symbol_count=1,
            )
        )

    with factory() as session:
        loaded = load_latest_snapshot(
            session,
            clock=lambda: stale_fetched_at + timedelta(hours=7),
            max_age_seconds=21600.0,
        )
        assert loaded is None


def test_load_latest_snapshot_returns_none_when_empty(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory() as session:
        loaded = load_latest_snapshot(
            session, clock=lambda: datetime.now(UTC), max_age_seconds=21600.0
        )
        assert loaded is None


def test_run_normalization_is_idempotent(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(602, None)})
    )
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=602, topic_id=0, automation_authorization="GRANTED"))
    processor.process(_telegram_input(channel_id=602, message_id=1, text="hello"))

    first = run_normalization(factory)
    second = run_normalization(factory)

    assert first[0].processed_count == 1
    assert second[0].processed_count == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(NormalizedContentRow)) == 1


def test_run_normalization_version_bump_appends_new_row(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(603, None)})
    )
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=603, topic_id=0, automation_authorization="GRANTED"))
    processor.process(_telegram_input(channel_id=603, message_id=1, text="hello"))

    run_normalization(factory, version="v1")
    run_normalization(factory, version="v2")

    with factory() as session:
        versions = sorted(
            row.normalizer_version for row in session.scalars(select(NormalizedContentRow))
        )
        assert versions == ["v1", "v2"]


def test_normalized_content_cannot_be_updated(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(604, None)})
    )
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=604, topic_id=0, automation_authorization="GRANTED"))
    processor.process(_telegram_input(channel_id=604, message_id=1, text="hello"))
    run_normalization(factory)

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE normalized_content SET normalized_text='changed'"))


def test_normalized_content_is_deleted_when_raw_row_is_purged(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    media_root = tmp_path / "media"
    processor = TelegramMessageProcessor(factory, MediaStore(media_root), frozenset({(605, None)}))
    now = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=605, topic_id=0, raw_retention_days=1, automation_authorization="GRANTED"
            )
        )
    processor.process(
        _telegram_input(
            channel_id=605, message_id=1, received_at=now - timedelta(days=5), text="hello"
        )
    )
    run_normalization(factory)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(NormalizedContentRow)) == 1

    run_retention_cleanup(factory, MediaStore(media_root), now=lambda: now)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(NormalizedContentRow)) == 0


def test_run_normalization_flags_media_for_manual_review(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(606, None)})
    )
    with factory.begin() as session:
        session.add(_channel_policy(channel_id=606, topic_id=0, automation_authorization="GRANTED"))
    processor.process(
        _telegram_input(
            channel_id=606,
            message_id=1,
            content_type="image",
            media_bytes=b"chart-bytes",
            media_filename="chart.png",
        )
    )

    run_normalization(factory)

    with factory() as session:
        row = session.scalar(select(NormalizedContentRow))
        assert row is not None
        assert row.media_review_status == "PENDING_MANUAL_REVIEW"
        assert row.normalized_text == ""


def test_run_normalization_skips_target_with_revoked_authorization(
    engine: Engine, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    factory = create_session_factory(engine)
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(607, None)})
    )
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=607,
                topic_id=0,
                automation_authorization="GRANTED",
                gate_decision="PAUSED",
            )
        )
    processor.process(_telegram_input(channel_id=607, message_id=1, text="hello"))

    with caplog.at_level(logging.WARNING, logger="telegram_trader.normalize_content"):
        results = run_normalization(factory)

    assert results == []
    assert any(
        "excludes target from normalization" in record.getMessage() for record in caplog.records
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(NormalizedContentRow)) == 0


def _run_pipeline(factory, channel_id: int, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    """Process one raw message and normalize it, so a signal test has real input to parse."""
    topic_id = overrides.get("topic_id")
    assert topic_id is None or isinstance(topic_id, int)
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(channel_id, topic_id)})
    )
    processor.process(_telegram_input(channel_id=channel_id, **overrides))
    run_normalization(factory)


def _result_for(results: list[SignalParsingResult], channel_id: int) -> SignalParsingResult:
    return next(r for r in results if r.channel_id == channel_id)


def test_run_signal_parsing_reaches_validated_for_static_allowlist(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=700,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    _run_pipeline(factory, 700, message_id=1, text="BTC 多 市價進場")

    results = run_signal_parsing(factory)

    assert _result_for(results, 700).processed_count == 1
    with factory() as session:
        row = session.scalar(
            select(NormalizedSignalRow).where(NormalizedSignalRow.channel_id == 700)
        )
        assert row is not None
        assert row.status == "VALIDATED"
        assert row.symbol == "BTCUSDT"
        assert row.side == "LONG"
        assert row.revision == 0
        assert row.link_method is None


def test_run_signal_parsing_dynamic_scope_never_reaches_validated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=701,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
    _run_pipeline(factory, 701, message_id=1, text="ARB 空 市價進場附近0.1950")

    run_signal_parsing(factory)

    with factory() as session:
        row = session.scalar(
            select(NormalizedSignalRow).where(NormalizedSignalRow.channel_id == 701)
        )
        assert row is not None
        assert row.status == "NEW"
        assert row.side == "SHORT"


def test_run_signal_parsing_missing_side_is_incomplete(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=702,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
    _run_pipeline(factory, 702, message_id=1, text="BTC 50倍 進場0.1686")

    run_signal_parsing(factory)

    with factory() as session:
        row = session.scalar(
            select(NormalizedSignalRow).where(NormalizedSignalRow.channel_id == 702)
        )
        assert row is not None
        assert row.status == "INCOMPLETE"
        assert row.side is None


def test_run_signal_parsing_promotional_message_produces_no_row(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=703,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
    _run_pipeline(factory, 703, message_id=1, text="$BTC 衝高已經翻倍了,恭喜早期跟上車的朋友們")

    results = run_signal_parsing(factory)

    assert _result_for(results, 703).processed_count == 0
    assert _result_for(results, 703).skipped_count == 1
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 703)
            )
            == 0
        )


def test_run_signal_parsing_reply_cancel_creates_linked_revision(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=704,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(704, None)})
    )
    processor.process(
        _telegram_input(
            channel_id=704, message_id=1, text="BTC 多 市價進場", source_date=now, received_at=now
        )
    )
    processor.process(
        _telegram_input(
            channel_id=704,
            message_id=2,
            text="取消",
            reply_to_message_id=1,
            source_date=now + timedelta(minutes=1),
            received_at=now + timedelta(minutes=1),
        )
    )
    run_normalization(factory)

    run_signal_parsing(factory)

    with factory() as session:
        rows = list(
            session.scalars(
                select(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 704)
                .order_by(NormalizedSignalRow.revision)
            )
        )
        assert len(rows) == 2
        origin, follow_up = rows
        assert origin.revision == 0
        assert follow_up.revision == 1
        assert follow_up.signal_id == origin.signal_id
        assert follow_up.status == "CANCELLED"
        assert follow_up.symbol == origin.symbol
        assert follow_up.side == origin.side
        assert follow_up.link_method == "REPLY"


def test_run_signal_parsing_reply_with_new_stop_updates_stop_only(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=705,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(705, None)})
    )
    processor.process(
        _telegram_input(
            channel_id=705, message_id=1, text="BTC 多 市價進場", source_date=now, received_at=now
        )
    )
    processor.process(
        _telegram_input(
            channel_id=705,
            message_id=2,
            text="止損6.5",
            reply_to_message_id=1,
            source_date=now + timedelta(minutes=1),
            received_at=now + timedelta(minutes=1),
        )
    )
    run_normalization(factory)

    run_signal_parsing(factory)

    with factory() as session:
        rows = list(
            session.scalars(
                select(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 705)
                .order_by(NormalizedSignalRow.revision)
            )
        )
        assert len(rows) == 2
        origin, follow_up = rows
        assert follow_up.signal_id == origin.signal_id
        assert follow_up.symbol == origin.symbol
        assert follow_up.side == origin.side
        assert follow_up.status == origin.status
        assert follow_up.stop_value == pytest.approx(Decimal("6.5"))
        assert follow_up.stop_origin == "AUTHOR"
        assert follow_up.link_method == "REPLY"


def test_run_signal_parsing_is_idempotent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=706,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    _run_pipeline(factory, 706, message_id=1, text="BTC 多 市價進場")

    first = run_signal_parsing(factory)
    second = run_signal_parsing(factory)

    assert _result_for(first, 706).processed_count == 1
    assert _result_for(second, 706).processed_count == 0
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 706)
            )
            == 1
        )


def test_run_signal_parsing_version_bump_appends_new_row(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=707,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    _run_pipeline(factory, 707, message_id=1, text="BTC 多 市價進場")

    run_signal_parsing(factory, version="v1")
    run_signal_parsing(factory, version="v2")

    with factory() as session:
        versions = sorted(
            row.parser_version
            for row in session.scalars(
                select(NormalizedSignalRow).where(NormalizedSignalRow.channel_id == 707)
            )
        )
        assert versions == ["v1", "v2"]


def test_normalized_signal_cannot_be_updated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=708,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    _run_pipeline(factory, 708, message_id=1, text="BTC 多 市價進場")
    run_signal_parsing(factory)

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE normalized_signals SET status='CANCELLED'"))


def test_normalized_signal_is_deleted_when_raw_row_is_purged(engine: Engine) -> None:
    factory = create_session_factory(engine)
    now = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=709,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                raw_retention_days=1,
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    _run_pipeline(
        factory, 709, message_id=1, received_at=now - timedelta(days=5), text="BTC 多 市價進場"
    )
    run_signal_parsing(factory)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 709)
            )
            == 1
        )

    run_retention_cleanup(factory, MediaStore(Path("media")), now=lambda: now)

    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 709)
            )
            == 0
        )


def test_run_signal_parsing_never_parses_analysis_channel(engine: Engine) -> None:
    """BR-001: only EXECUTION_SIGNAL channels are parsed; ANALYSIS is Phase 5 territory."""
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=710,
                topic_id=0,
                channel_type="ANALYSIS",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="MONITOR_ONLY",
            )
        )
    _run_pipeline(factory, 710, message_id=1, text="BTC 多 市價進場")

    results = run_signal_parsing(factory)

    assert results == []
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NormalizedSignalRow)
                .where(NormalizedSignalRow.channel_id == 710)
            )
            == 0
        )


def _channel_policy_execution_signal(channel_id: int, **overrides: object) -> ChannelPolicy:
    overrides.setdefault("channel_type", "EXECUTION_SIGNAL")
    overrides.setdefault("symbol_scope_mode", "STATIC_ALLOWLIST")
    overrides.setdefault("allowed_symbols", ["BTCUSDT"])
    overrides.setdefault("automation_authorization", "GRANTED")
    overrides.setdefault("gate_decision", "ENABLED")
    return _channel_policy(channel_id=channel_id, topic_id=0, **overrides)


# One hour after `_telegram_input`'s default fixed `source_date` (2026-09-06
# 08:00) -- safely inside the signal's 24h `expires_at` window regardless of
# the real wall-clock date the test suite happens to run on.
SIGNAL_FIXTURE_NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)


def test_load_pending_signals_returns_latest_revision_without_existing_request(
    engine: Engine,
) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(800))
    _run_pipeline(factory, 800, message_id=1, text="BTC 多 市價進場")
    run_signal_parsing(factory)

    with factory() as session:
        pending = load_pending_signals(session, now=SIGNAL_FIXTURE_NOW)

    assert [row.channel_id for row in pending] == [800]


def test_load_pending_signals_excludes_already_expired_signal(engine: Engine) -> None:
    """A `parser_version` bump re-parsing weeks-old backlog must not surface

    a signal whose 24h `expires_at` window (relative to its own message's
    real `source_date`) has already passed -- otherwise every version bump
    would flood a stale notification for old, no-longer-actionable trades.
    """
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(802))
    _run_pipeline(factory, 802, message_id=1, text="BTC 多 市價進場")
    run_signal_parsing(factory)

    with factory() as session:
        still_pending = load_pending_signals(session, now=SIGNAL_FIXTURE_NOW)
        assert [row.channel_id for row in still_pending] == [802]

        after_expiry = load_pending_signals(session, now=SIGNAL_FIXTURE_NOW + timedelta(hours=24))
        assert after_expiry == []


def test_create_request_then_load_pending_excludes_it(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(801))
    _run_pipeline(factory, 801, message_id=1, text="BTC 多 市價進場")
    run_signal_parsing(factory)

    with factory.begin() as session:
        (signal,) = load_pending_signals(session, now=SIGNAL_FIXTURE_NOW)
        create_request(session, signal, nonce="nonce-1", telegram_message_id=42)

    with factory() as session:
        assert load_pending_signals(session, now=SIGNAL_FIXTURE_NOW) == []
        request = session.get(
            SignalDecisionRequest, request_id_for_signal_row(signal.signal_row_id)
        )
        assert request is not None
        assert request.telegram_message_id == 42
        assert request.expires_at == signal.expires_at


def _create_signal_and_request(factory, channel_id: int, text: str = "BTC 多 市價進場"):  # type: ignore[no-untyped-def]
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(channel_id))
    _run_pipeline(factory, channel_id, message_id=1, text=text)
    run_signal_parsing(factory)
    with factory.begin() as session:
        (signal,) = load_pending_signals(session, now=SIGNAL_FIXTURE_NOW)
        create_request(session, signal, nonce="nonce-1", telegram_message_id=42)
        request_id = request_id_for_signal_row(signal.signal_row_id)
    return request_id


def test_record_decision_approved_happy_path(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 802)

    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    assert outcome == "APPROVED"


def test_record_decision_rejected_happy_path(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 803)

    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="REJECT",
            now=SIGNAL_FIXTURE_NOW,
        )

    assert outcome == "REJECTED"


def test_record_decision_stale_when_already_decided(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 804)
    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    with factory.begin() as session:
        second_outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-2",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    assert second_outcome == "REJECTED_STALE"


def test_record_decision_stale_when_newer_revision_exists(engine: Engine) -> None:
    factory = create_session_factory(engine)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(805))
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(805, None)})
    )
    processor.process(
        _telegram_input(
            channel_id=805, message_id=1, text="BTC 多 市價進場", source_date=now, received_at=now
        )
    )
    run_normalization(factory)
    run_signal_parsing(factory)
    with factory.begin() as session:
        (signal,) = load_pending_signals(session, now=now)
        create_request(session, signal, nonce="nonce-1", telegram_message_id=42)
        request_id = request_id_for_signal_row(signal.signal_row_id)

    # A reply-cancel arrives after the request was created, superseding the revision.
    processor.process(
        _telegram_input(
            channel_id=805,
            message_id=2,
            text="取消",
            reply_to_message_id=1,
            source_date=now + timedelta(minutes=1),
            received_at=now + timedelta(minutes=1),
        )
    )
    run_normalization(factory)
    run_signal_parsing(factory)

    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=now + timedelta(minutes=2),
        )

    assert outcome == "REJECTED_STALE"


def test_record_decision_expired(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 806)

    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        past_expiry = request.expires_at + timedelta(seconds=1)
        outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=past_expiry,
        )

    assert outcome == "REJECTED_EXPIRED"


def test_record_decision_idempotent_same_callback_query_id(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 807)

    with factory.begin() as session:
        first = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-duplicate",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )
    with factory.begin() as session:
        second = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-duplicate",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    assert first == "APPROVED"
    assert second == "APPROVED"
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SignalDecisionEvent)
                .where(SignalDecisionEvent.request_id == request_id)
            )
            == 1
        )


def test_record_decision_unknown_request_returns_none(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id="does-not-exist",
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
        )

    assert outcome is None


def test_signal_decision_request_cannot_be_updated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _create_signal_and_request(factory, 808)

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE signal_decision_requests SET nonce='changed'"))


def test_signal_decision_event_cannot_be_updated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 809)
    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
        )

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE signal_decision_events SET outcome='REJECTED'"))


# --- editable order drafts (stop-loss/take-profit) ---


def test_load_current_draft_falls_back_to_original_signal_when_never_edited(
    engine: Engine,
) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(
        factory,
        811,
        text="BTC 多 市價進場 請大家耐心等待後續走勢確認並持續觀察 止損 59000 止盈 62000",
    )

    with factory() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        draft = load_current_draft(session, request)

    assert draft.stop_value == Decimal("59000")
    assert draft.take_profits == [Decimal("62000")]


def test_create_edit_stop_carries_forward_prior_take_profit_edit(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 812)

    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        create_edit(session, request, field="TAKE_PROFIT", value=[Decimal("70000")])

    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        draft = create_edit(session, request, field="STOP", value=Decimal("55000"))

    assert draft.stop_value == Decimal("55000")
    assert draft.take_profits == [Decimal("70000")]

    with factory() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        current = load_current_draft(session, request)
    assert current.stop_value == Decimal("55000")
    assert current.take_profits == [Decimal("70000")]


def test_record_decision_uses_edited_draft_values(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 813)

    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        create_edit(session, request, field="STOP", value=Decimal("58000"))

    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    with factory() as session:
        event = session.scalar(
            select(SignalDecisionEvent).where(SignalDecisionEvent.request_id == request_id)
        )
        assert event is not None
        assert event.approved_stop_value == Decimal("58000")


def test_record_decision_uses_original_values_when_never_edited(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(
        factory,
        814,
        text="BTC 多 市價進場 請大家耐心等待後續走勢確認並持續觀察 止損 59000 止盈 62000",
    )

    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    with factory() as session:
        event = session.scalar(
            select(SignalDecisionEvent).where(SignalDecisionEvent.request_id == request_id)
        )
        assert event is not None
        assert event.approved_stop_value == Decimal("59000")
        assert event.approved_take_profits == ["62000"]


def test_signal_decision_edit_cannot_be_updated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 815)
    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        create_edit(session, request, field="STOP", value=Decimal("58000"))

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE signal_decision_edits SET stop_value=99999"))


def test_signal_decision_edit_cascade_deletes_via_retention_cleanup(engine: Engine) -> None:
    factory = create_session_factory(engine)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(816, raw_retention_days=1))
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(816, None)})
    )
    processor.process(
        _telegram_input(
            channel_id=816,
            message_id=1,
            text="BTC 多 市價進場",
            source_date=now - timedelta(days=5),
            received_at=now - timedelta(days=5),
        )
    )
    run_normalization(factory)
    run_signal_parsing(factory)
    with factory.begin() as session:
        # Requested right after the message's own source_date (well inside its
        # 24h expires_at window), not the outer `now` used for retention below.
        (signal,) = load_pending_signals(session, now=now - timedelta(days=5, hours=-1))
        create_request(session, signal, nonce="nonce-1", telegram_message_id=42)
        request_id = request_id_for_signal_row(signal.signal_row_id)
    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        create_edit(session, request, field="STOP", value=Decimal("58000"))

    run_retention_cleanup(factory, MediaStore(Path("media")), now=lambda: now)

    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SignalDecisionEdit)
                .where(SignalDecisionEdit.request_id == request_id)
            )
            == 0
        )


def test_signal_decision_rows_cascade_delete_via_retention_cleanup(engine: Engine) -> None:
    factory = create_session_factory(engine)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(810, raw_retention_days=1))
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(810, None)})
    )
    processor.process(
        _telegram_input(
            channel_id=810,
            message_id=1,
            text="BTC 多 市價進場",
            source_date=now - timedelta(days=5),
            received_at=now - timedelta(days=5),
        )
    )
    run_normalization(factory)
    run_signal_parsing(factory)
    with factory.begin() as session:
        (signal,) = load_pending_signals(session, now=now - timedelta(days=5, hours=-1))
        create_request(session, signal, nonce="nonce-1", telegram_message_id=42)
        request_id = request_id_for_signal_row(signal.signal_row_id)
    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
        )

    run_retention_cleanup(factory, MediaStore(Path("media")), now=lambda: now)

    with factory() as session:
        assert session.get(SignalDecisionRequest, request_id) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(SignalDecisionEvent)
                .where(SignalDecisionEvent.request_id == request_id)
            )
            == 0
        )


# --- Phase 5 Slice 2a: Thesis extraction ---


def _channel_policy_analysis(channel_id: int, **overrides: object) -> ChannelPolicy:
    overrides.setdefault("channel_type", "ANALYSIS")
    overrides.setdefault("symbol_scope_mode", "STATIC_ALLOWLIST")
    overrides.setdefault("allowed_symbols", ["BTCUSDT"])
    overrides.setdefault("automation_authorization", "GRANTED")
    overrides.setdefault("ai_authorization", "GRANTED")
    overrides.setdefault("media_authorization", "GRANTED")
    overrides.setdefault("gate_decision", "MONITOR_ONLY")
    return _channel_policy(channel_id=channel_id, topic_id=0, **overrides)


_THESIS_SOURCE_TEXT = "BTC 若跌破773將測試760-756支撐"


def _thesis_response(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "primary_direction": "NEUTRAL",
        "confidence_status": "HIGH",
        "evidence_quotes": ["若跌破773將測試760-756支撐"],
        "conditions": [
            {
                "comparator": "BELOW",
                "trigger_price": "773",
                "symbol": "BTCUSDT",
                "invalidates_thesis": True,
                "implication_direction": "DOWN",
                "target_zone_low": "756",
                "target_zone_high": "760",
                "evidence_quote": "若跌破773將測試760-756支撐",
            }
        ],
    }
    values.update(overrides)
    return values


class _FakeOpenAiClient:
    """Implements only `extract_structured`, matching the project's FakeXClient convention."""

    def __init__(self, responses: list[object] | None = None) -> None:
        self._responses = list(responses) if responses is not None else None
        self.calls: list[dict[str, object]] = []

    def extract_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, object],
        schema_name: str,
    ) -> dict[str, object]:
        self.calls.append(
            {"model": model, "user_content": user_content, "schema_name": schema_name}
        )
        if self._responses is not None:
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            assert isinstance(response, dict)
            return response
        return _thesis_response()


def _seed_thesis_candidate(  # type: ignore[no-untyped-def]
    factory, channel_id: int, *, text: str = _THESIS_SOURCE_TEXT, with_media: bool = False
) -> None:
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(channel_id, None)})
    )
    overrides: dict[str, object] = {"channel_id": channel_id, "message_id": 1, "text": text}
    if with_media:
        overrides.update(
            content_type="caption",
            media_bytes=b"fake-chart-bytes",
            media_filename="chart.png",
            media_mime_type="image/png",
        )
    processor.process(_telegram_input(**overrides))
    run_normalization(factory)


def test_run_thesis_extraction_happy_path(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(900))
    _seed_thesis_candidate(factory, 900)
    client = _FakeOpenAiClient()

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results == [
        ThesisExtractionResult(channel_id=900, topic_id=0, processed_count=1, skipped_count=0)
    ]
    assert len(client.calls) == 1
    assert client.calls[0]["user_content"] == _THESIS_SOURCE_TEXT
    with factory() as session:
        row = session.scalar(select(ThesisRow))
        assert row is not None
        assert row.status == "DRAFT"
        assert row.primary_direction == "NEUTRAL"
        assert row.media_included is False
        assert row.conditions[0]["trigger_price"] == "773"


def test_run_thesis_extraction_zero_calls_when_ai_authorization_revoked(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(901, ai_authorization="REVOKED"))
    _seed_thesis_candidate(factory, 901)
    client = _FakeOpenAiClient()

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results == []
    assert client.calls == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ThesisRow)) == 0


def test_run_thesis_extraction_zero_calls_when_media_blocked(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(902, media_authorization="REVOKED"))
    _seed_thesis_candidate(factory, 902, with_media=True)
    client = _FakeOpenAiClient()

    with factory() as session:
        seeded = session.scalar(select(NormalizedContentRow))
        assert seeded is not None and seeded.media_review_status == "PENDING_MANUAL_REVIEW"

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results[0].processed_count == 0
    assert results[0].skipped_count == 1
    assert client.calls == []


def test_run_thesis_extraction_media_authorization_granted_allows_processing(
    engine: Engine,
) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(903, media_authorization="GRANTED"))
    _seed_thesis_candidate(factory, 903, with_media=True)
    client = _FakeOpenAiClient()

    with factory() as session:
        seeded = session.scalar(select(NormalizedContentRow))
        assert seeded is not None and seeded.media_review_status == "PENDING_MANUAL_REVIEW"

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results[0].processed_count == 1
    assert len(client.calls) == 1
    assert "chart.png" not in client.calls[0]["user_content"]  # type: ignore[operator]
    with factory() as session:
        row = session.scalar(select(ThesisRow))
        assert row is not None
        assert row.media_included is False


def test_run_thesis_extraction_skips_message_without_valid_symbol(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(904))
    _seed_thesis_candidate(factory, 904, text="今天是個好日子，適合觀望")  # noqa: RUF001
    client = _FakeOpenAiClient()

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results[0].processed_count == 0
    assert results[0].skipped_count == 1
    assert client.calls == []


def test_run_thesis_extraction_malformed_response_persists_nothing(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(905))
    _seed_thesis_candidate(factory, 905)
    client = _FakeOpenAiClient(responses=[_thesis_response(primary_direction="SIDEWAYS")])

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results[0].processed_count == 0
    assert results[0].skipped_count == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ThesisRow)) == 0


def test_run_thesis_extraction_openai_error_persists_nothing(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(906))
    _seed_thesis_candidate(factory, 906)
    client = _FakeOpenAiClient(responses=[OpenAiClientError("transient failure")])

    results = run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert results[0].processed_count == 0
    assert results[0].skipped_count == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ThesisRow)) == 0


def test_run_thesis_extraction_is_idempotent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(907))
    _seed_thesis_candidate(factory, 907)
    client = _FakeOpenAiClient()

    run_thesis_extraction(factory, client, model="gpt-4o-mini")
    run_thesis_extraction(factory, client, model="gpt-4o-mini")

    assert len(client.calls) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ThesisRow)) == 1


def test_run_thesis_extraction_model_bump_appends_new_row(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(908))
    _seed_thesis_candidate(factory, 908)
    client = _FakeOpenAiClient()

    run_thesis_extraction(factory, client, model="gpt-4o-mini")
    run_thesis_extraction(factory, client, model="gpt-4o")

    with factory() as session:
        models = sorted(row.llm_model for row in session.scalars(select(ThesisRow)))
        assert models == ["gpt-4o", "gpt-4o-mini"]


def test_thesis_cannot_be_updated(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(909))
    _seed_thesis_candidate(factory, 909)
    run_thesis_extraction(factory, _FakeOpenAiClient(), model="gpt-4o-mini")

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE thesis SET status='INSUFFICIENT_DATA'"))


def test_thesis_is_deleted_when_normalized_content_is_purged(engine: Engine) -> None:
    factory = create_session_factory(engine)
    now = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(910, raw_retention_days=1))
    processor = TelegramMessageProcessor(
        factory, MediaStore(Path("media")), frozenset({(910, None)})
    )
    processor.process(
        _telegram_input(
            channel_id=910,
            message_id=1,
            text=_THESIS_SOURCE_TEXT,
            source_date=now - timedelta(days=5),
            received_at=now - timedelta(days=5),
        )
    )
    run_normalization(factory)
    run_thesis_extraction(factory, _FakeOpenAiClient(), model="gpt-4o-mini")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ThesisRow)) == 1

    run_retention_cleanup(factory, MediaStore(Path("media")), now=lambda: now)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ThesisRow)) == 0
