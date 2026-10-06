from __future__ import annotations

import asyncio
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
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader import channel_admin, runtime_settings
from telegram_trader import cli as cli_module
from telegram_trader.binance_market_data import (
    compute_snapshot_id,
    load_latest_snapshot,
    refresh_snapshot,
)
from telegram_trader.binance_trading_client import BinanceApiError
from telegram_trader.channel_policy import (
    ChannelPolicyError,
    evaluate_raw_collection,
    load_channel_policies,
    resolve_effective_targets,
    targets_with_policies,
)
from telegram_trader.config import Settings, TelegramChannelTarget, get_settings
from telegram_trader.control_bot import (
    ControlBot,
    encode_daily_loss_callback,
    encode_handled_callback,
)
from telegram_trader.daily_loss import (
    DAILY_LOSS_EVENT_TYPE,
    DailyLossLimit,
    active_pause,
    alert_event_id,
    alert_if_daily_loss_limit_reached,
    record_pause_decision,
    utc_day_start,
)
from telegram_trader.db import create_db_engine, create_session_factory, database_is_ready
from telegram_trader.execution_gateway import _execute_one, load_eligible_intents
from telegram_trader.execution_worker import StepHealth
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
    ExchangeOrder,
    MockMessageReceipt,
    OrderLifecycle,
    OutboxDeliveryReceipt,
    OutboxEvent,
    RealizedPnl,
    RiskConfigSnapshot,
    RuntimeSettingChange,
    SignalDecisionEdit,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TelegramCollectorCheckpoint,
    TelegramMessageVersion,
)
from telegram_trader.models import NormalizedContent as NormalizedContentRow
from telegram_trader.models import NormalizedSignal as NormalizedSignalRow
from telegram_trader.models import RiskDecision as RiskDecisionRow
from telegram_trader.models import TakeProfitOrder as TakeProfitOrderRow
from telegram_trader.models import Thesis as ThesisRow
from telegram_trader.models import TradeIntent as TradeIntentRow
from telegram_trader.normalize_content import NormalizationResult, run_normalization
from telegram_trader.openai_client import OpenAiClientError
from telegram_trader.outbox import OutboxConsumer, append_outbox_event
from telegram_trader.parse_signals import SignalParsingResult, run_signal_parsing
from telegram_trader.position_monitor import run_position_monitor
from telegram_trader.retention_cleanup import retention_migration_applied, run_retention_cleanup
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
from telegram_trader.trade_intent_pipeline import run_risk_evaluation
from telegram_trader.web_console import records as console_records

pytestmark = pytest.mark.integration


@pytest.fixture
def engine() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    integration_settings = Settings(database_url=database_url) if database_url else Settings()
    active_engine = create_db_engine(integration_settings)
    # The TRUNCATE below CASCADEs into trade_intent/order_lifecycle/exchange_order,
    # so pointing this suite at the real database erases real execution records.
    database_name = active_engine.url.database or ""
    if not database_name.endswith("_test"):
        active_engine.dispose()
        pytest.fail(
            f"refusing to TRUNCATE database {database_name!r}: integration tests "
            "must target a database whose name ends with '_test'"
        )
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
                "channel_policies, trading_pause_decisions, runtime_setting_changes "
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
        assert row.raw_retention_days == 60


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


# --- Phase 6 Slice 1: Risk Engine (trade_intent / risk_decision) ---

# 30s after `_telegram_input`'s default fixed `source_date` (2026-09-06
# 08:00) -- inside both the signal's 24h expires_at window AND BR-006's much
# tighter 60s source-age / 10s receive-lag freshness window (unlike
# SIGNAL_FIXTURE_NOW, which is 1 hour later -- fine for expires_at, much too
# late for BR-006).
RISK_NOW = datetime(2026, 9, 6, 8, 0, 30, tzinfo=UTC)


class _FakeRiskMarketDataClient:
    def __init__(self, price: Decimal | None = Decimal("100")) -> None:
        self._price = price

    def get_mark_price(self, symbol: str) -> dict[str, str]:
        if self._price is None:
            return {}  # no markPrice key -- fetch_mark_price raises, pipeline fails closed
        return {"markPrice": str(self._price)}

    def get_klines(self, symbol: str, *, start_time_ms: int, limit: int = 1) -> list[list[object]]:
        if self._price is None:
            return []  # empty -- fetch_reference_price raises, pipeline fails closed
        # Same price as get_mark_price -- zero deviation, so tests that only
        # care about some other rule don't incidentally trip BR-006 too.
        return [
            [start_time_ms, str(self._price), str(self._price), str(self._price), str(self._price)]
        ]


def _risk_settings(equity: Decimal = Decimal("10000")) -> Settings:
    return Settings(risk_equity_baseline_usdt=equity)


def _approve_signal(  # type: ignore[no-untyped-def]
    factory, channel_id: int, text: str = "BTC 多 市價進場", *, nonce: str = "nonce-risk"
) -> None:
    """EXECUTION_SIGNAL channel, STATIC_ALLOWLIST BTCUSDT -- reaches VALIDATED,

    then approved via the Control Bot's own `record_decision`, exactly the
    real trigger `run_risk_evaluation` consumes.
    """
    with factory.begin() as session:
        session.add(_channel_policy_execution_signal(channel_id))
    _run_pipeline(factory, channel_id, message_id=1, text=text)
    run_signal_parsing(factory)
    with factory.begin() as session:
        (signal,) = load_pending_signals(session, now=RISK_NOW)
        create_request(session, signal, nonce=nonce, telegram_message_id=42)
        request_id = request_id_for_signal_row(signal.signal_row_id)
    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id=f"cbq-{nonce}",
            action="APPROVE",
            now=RISK_NOW,
        )
    assert outcome == "APPROVED"


def test_run_risk_evaluation_approves_clean_signal(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 820)

    results = run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )

    assert len(results) == 1
    assert results[0].verdict == "APPROVED"
    assert results[0].symbol == "BTCUSDT"
    with factory() as session:
        intent = session.scalar(select(TradeIntentRow).where(TradeIntentRow.channel_id == 820))
        assert intent is not None
        assert intent.status == "RISK_APPROVED"
        assert intent.symbol == "BTCUSDT"
        assert intent.side == "LONG"
        assert intent.stop_value is None  # never authored, DEFAULT_ROE_30

        decision = session.scalar(
            select(RiskDecisionRow).where(RiskDecisionRow.intent_id == intent.intent_id)
        )
        assert decision is not None
        assert decision.verdict == "APPROVED"
        assert decision.reason_codes == []
        assert decision.computed_stop_price == Decimal("94")  # ROE-30% baseline @ entry=100
        assert decision.quantity is not None and decision.quantity > 0
        assert decision.entry_price_used == Decimal("100")

        config_row = session.scalar(select(RiskConfigSnapshot))
        assert config_row is not None
        assert config_row.leverage == 5


def test_run_risk_evaluation_is_idempotent(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 821)

    first = run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )
    second = run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )

    assert len(first) == 1
    assert len(second) == 0
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TradeIntentRow)
                .where(TradeIntentRow.channel_id == 821)
            )
            == 1
        )


def test_run_risk_evaluation_rejects_approved_but_never_validated_signal(engine: Engine) -> None:
    """Real gap found running this against the real database (see risk_engine.py's

    REASON_SIGNAL_NOT_VALIDATED): the Control Bot notifies (and the user may
    approve) both NEW and VALIDATED signals, but only VALIDATED means the
    symbol actually resolved against real market data -- a NEW dynamic-scope
    signal's `symbol` can be bare, unresolved text (e.g. "ARB", not "ARBUSDT"),
    which Binance's own price endpoint would reject with a raw HTTP 400 if
    ever queried. Must be rejected explicitly before ever calling out.
    """
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=826,
                topic_id=0,
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    _run_pipeline(factory, 826, message_id=1, text="ARB 空 市價進場附近0.1950")
    run_signal_parsing(factory)
    with factory() as session:
        row = session.scalar(
            select(NormalizedSignalRow).where(NormalizedSignalRow.channel_id == 826)
        )
        assert row is not None
        assert row.status == "NEW"  # never resolved -- no Binance snapshot in this test

    with factory.begin() as session:
        (signal,) = load_pending_signals(session, now=RISK_NOW)
        create_request(session, signal, nonce="nonce-notvalidated", telegram_message_id=42)
        request_id = request_id_for_signal_row(signal.signal_row_id)
    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-notvalidated",
            action="APPROVE",
            now=RISK_NOW,
        )
    assert outcome == "APPROVED"

    market_client = _FakeRiskMarketDataClient(Decimal("100"))
    results = run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=market_client,
        clock=lambda: RISK_NOW,
    )

    assert len(results) == 1
    assert results[0].verdict == "REJECTED"
    assert results[0].reason_codes == ["SIGNAL_NOT_VALIDATED"]
    with factory() as session:
        intent = session.scalar(select(TradeIntentRow).where(TradeIntentRow.channel_id == 826))
        assert intent is not None
        assert intent.status == "RISK_REJECTED"


def test_run_risk_evaluation_rejects_when_mark_price_unavailable(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 822)

    results = run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(None),
        clock=lambda: RISK_NOW,
    )

    assert len(results) == 1
    assert results[0].verdict == "REJECTED"
    assert results[0].reason_codes == ["STALE_MARKET_DATA"]
    with factory() as session:
        intent = session.scalar(select(TradeIntentRow).where(TradeIntentRow.channel_id == 822))
        assert intent is not None
        assert intent.status == "RISK_REJECTED"
        decision = session.scalar(
            select(RiskDecisionRow).where(RiskDecisionRow.intent_id == intent.intent_id)
        )
        assert decision is not None
        assert decision.quantity is None
        assert decision.computed_stop_price is None


def test_run_risk_evaluation_requires_configured_equity_baseline(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 823)

    with pytest.raises(ValueError, match="RISK_EQUITY_BASELINE_USDT"):
        run_risk_evaluation(
            factory,
            settings=Settings(),  # risk_equity_baseline_usdt left unset
            market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
            clock=lambda: RISK_NOW,
        )


def test_trade_intent_and_risk_decision_are_append_only(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 824)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )

    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE trade_intent SET status='CANCELLED'"))
    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE risk_decision SET verdict='REJECTED'"))
    with engine.connect() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE risk_config_snapshot SET leverage=10"))


def test_risk_decision_reject_requires_reason_codes(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 825)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(None),
        clock=lambda: RISK_NOW,
    )

    with factory() as session:
        rejected = session.scalar(
            select(RiskDecisionRow).where(RiskDecisionRow.verdict == "REJECTED")
        )
        assert rejected is not None
        assert len(rejected.reason_codes) > 0

    with (
        engine.connect() as connection,
        pytest.raises(IntegrityError, match="ck_risk_decision_reject_has_reasons"),
        connection.begin(),
    ):
        connection.execute(
            text(
                "INSERT INTO risk_decision (decision_id, intent_id, verdict, reason_codes, "
                "config_snapshot_id, expires_at) "
                "SELECT 'bad-decision', intent_id, 'REJECTED', '[]'::jsonb, "
                "(SELECT config_snapshot_id FROM risk_config_snapshot LIMIT 1), "
                "now() + interval '1 hour' FROM trade_intent LIMIT 1"
            )
        )


class _TradingClientThatMustNotBeCalled:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Binance must not be touched, but {name!r} was called")


def test_execute_one_skips_intent_already_claimed_by_concurrent_run(engine: Engine) -> None:
    """P6-NOTICE-013: two runs can both load the same intent as eligible. Revision 0
    is the claim -- the run that loses the insert must return before any Binance call,
    or both submit the entry and a filled MARKET order gets filled a second time."""
    factory = create_session_factory(engine)
    _approve_signal(factory, 830)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )
    with factory() as session:
        ((trade_intent, risk_decision),) = load_eligible_intents(session)
    # The winning run's claim, committed after this run already loaded the intent.
    with factory.begin() as session:
        session.add(
            OrderLifecycle(
                lifecycle_row_id="concurrent-claim",
                intent_id=trade_intent.intent_id,
                revision=0,
                decision_id=risk_decision.decision_id,
                environment="TESTNET",
                symbol=trade_intent.symbol,
                side=trade_intent.side,
                entry_client_order_id="en-other-run",
                state="PENDING_SUBMIT",
                correlation_id="other-run",
                transitioned_at=RISK_NOW,
            )
        )

    result = _execute_one(
        factory,
        trade_intent,
        risk_decision,
        settings=_risk_settings(),
        market_data_client=_TradingClientThatMustNotBeCalled(),
        trading_client=_TradingClientThatMustNotBeCalled(),
        clock=lambda: RISK_NOW,
        poll_timeout_seconds=0,
        poll_interval_seconds=0,
        protection_deadline_seconds=0,
    )

    assert result.final_state == "SKIPPED"
    with factory() as session:
        lifecycle_rows = session.scalars(
            select(OrderLifecycle).where(OrderLifecycle.intent_id == trade_intent.intent_id)
        ).all()
    assert [row.lifecycle_row_id for row in lifecycle_rows] == ["concurrent-claim"]


class _FakeExecutionMarketDataClient:
    def get_exchange_info(self) -> dict[str, object]:
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }
            ]
        }


class _ProtectionFailsOnceTradingClient:
    """Entry fills at once; the first STOP_MARKET placement is rejected, the retry lands."""

    def __init__(self) -> None:
        self.algo_client_ids: list[str] = []

    def get_position_risk(self) -> list[dict[str, object]]:
        return []

    def set_leverage(self, symbol: str, leverage: int) -> dict[str, object]:
        return {}

    def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, object]:
        return {}

    def get_order(self, symbol: str, *, orig_client_order_id: str) -> dict[str, object]:
        if not hasattr(self, "_entry_placed"):
            raise BinanceApiError(400, -2013, "Order does not exist.")
        return {"status": "FILLED", "orderId": 11, "executedQty": "1", "avgPrice": "100"}

    def place_order(self, **params: object) -> dict[str, object]:
        self._entry_placed = True
        return {"status": "FILLED", "orderId": 11, "executedQty": "1", "avgPrice": "100"}

    def place_algo_order(self, **params: object) -> dict[str, object]:
        self.algo_client_ids.append(str(params["clientAlgoId"]))
        if len(self.algo_client_ids) == 1:
            raise BinanceApiError(400, -4131, "rejected")
        return {"algoStatus": "NEW", "algoId": 22}

    def get_algo_order(self, *, client_algo_id: str) -> dict[str, object]:
        raise BinanceApiError(400, -2013, "Order does not exist.")


def test_execute_one_retries_failed_protection_and_notifies_each_attempt(engine: Engine) -> None:
    """2026-09-29, explicit user request: a failed stop placement is retried once, and
    both the first failure and the retry's outcome reach the user as notifications."""
    factory = create_session_factory(engine)
    _approve_signal(factory, 831)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )
    with factory() as session:
        ((trade_intent, risk_decision),) = load_eligible_intents(session)
    trading_client = _ProtectionFailsOnceTradingClient()

    result = _execute_one(
        factory,
        trade_intent,
        risk_decision,
        settings=_risk_settings(),
        market_data_client=_FakeExecutionMarketDataClient(),
        trading_client=trading_client,
        clock=lambda: RISK_NOW,
        poll_timeout_seconds=0,
        poll_interval_seconds=0,
        protection_deadline_seconds=5,
    )

    assert result.final_state == "PROTECTED"
    assert len(trading_client.algo_client_ids) == 2
    assert trading_client.algo_client_ids[0] != trading_client.algo_client_ids[1]
    with factory() as session:
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == trade_intent.intent_id,
                OutboxEvent.event_type.like("execution_%"),
            )
            .order_by(OutboxEvent.recorded_at, OutboxEvent.event_type)
        ).all()
    by_type = {event.event_type: event.payload for event in events}
    assert by_type["execution_protection_retrying"]["attempt"] == 1
    assert "rejected" in by_type["execution_protection_retrying"]["reason"]
    assert by_type["execution_protection_confirmed"]["attempt"] == 2
    assert "execution_protection_failed" not in by_type


class _EntryAlwaysFailsTradingClient(_ProtectionFailsOnceTradingClient):
    def __init__(self) -> None:
        super().__init__()
        self.place_order_calls = 0

    def get_order(self, symbol: str, *, orig_client_order_id: str) -> dict[str, object]:
        raise BinanceApiError(400, -2013, "Order does not exist.")

    def place_order(self, **params: object) -> dict[str, object]:
        self.place_order_calls += 1
        raise BinanceApiError(400, -2019, "Margin is insufficient.")


def test_execute_one_notifies_failed_entry_and_never_resubmits_it(engine: Engine) -> None:
    """2026-09-29, explicit user request: a failed entry is notified, not retried --
    neither within this run nor by a later one."""
    factory = create_session_factory(engine)
    _approve_signal(factory, 832)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )
    with factory() as session:
        ((trade_intent, risk_decision),) = load_eligible_intents(session)
    trading_client = _EntryAlwaysFailsTradingClient()

    result = _execute_one(
        factory,
        trade_intent,
        risk_decision,
        settings=_risk_settings(),
        market_data_client=_FakeExecutionMarketDataClient(),
        trading_client=trading_client,
        clock=lambda: RISK_NOW,
        poll_timeout_seconds=0,
        poll_interval_seconds=0,
        protection_deadline_seconds=5,
    )

    assert result.final_state == "ENTRY_FAILED"
    assert trading_client.place_order_calls == 1
    assert trading_client.algo_client_ids == []
    with factory() as session:
        (event,) = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == trade_intent.intent_id,
                OutboxEvent.event_type == "execution_entry_failed",
            )
        ).all()
        assert "Margin is insufficient" in event.payload["reason"]
        assert load_eligible_intents(session) == []


# --- Control Bot failure reminders (2026-09-29, explicit user request) ---


class _RecordingBotClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, user_id: int, text: str, buttons: object = None) -> object:
        self.sent.append((text, buttons))
        return type("_Message", (), {"id": len(self.sent)})()


class _CallbackEvent:
    def __init__(self, data: bytes) -> None:
        self.sender_id = 555
        self.data = data
        self.id = 1
        self.edits: list[tuple[str, object]] = []
        self.answers: list[tuple[str | None, bool]] = []

    async def edit(self, text: str, buttons: object = None) -> None:
        self.edits.append((text, buttons))

    async def answer(self, text: str | None = None, alert: bool = False) -> None:
        self.answers.append((text, alert))


def _control_bot(factory, client: _RecordingBotClient, minutes_later: float = 0):  # type: ignore[no-untyped-def]
    return ControlBot(
        client,
        factory,
        bot_token="placeholder",
        allowlisted_user_id=555,
        clock=lambda: datetime.now(UTC) + timedelta(minutes=minutes_later),
    )


def _append_execution_event(factory, event_type: str, event_id: str) -> None:  # type: ignore[no-untyped-def]
    with factory.begin() as session:
        append_outbox_event(
            session,
            event_id=event_id,
            event_type=event_type,
            aggregate_type="trade_intent",
            aggregate_id="intent-reminder",
            payload={"symbol": "XRPUSDT", "reason": "Margin is insufficient."},
        )


def test_control_bot_failure_gets_handled_button_success_does_not(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _append_execution_event(factory, "execution_entry_failed", "a" * 64)
    _append_execution_event(factory, "execution_entry_filled", "b" * 64)
    client = _RecordingBotClient()

    asyncio.run(_control_bot(factory, client).notify_execution_events())

    by_title = {text.splitlines()[0]: buttons for text, buttons in client.sent}
    assert by_title["✅ 進場單已成交"] is None
    failure_buttons = next(b for t, b in by_title.items() if t.startswith("❌ 進場單掛單失敗"))
    assert failure_buttons is not None


def test_control_bot_reminds_unhandled_failure_once_after_5_minutes(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _append_execution_event(factory, "execution_protection_failed", "c" * 64)
    _append_execution_event(factory, "execution_protection_confirmed", "d" * 64)
    client = _RecordingBotClient()
    asyncio.run(_control_bot(factory, client).notify_execution_events())

    assert (
        asyncio.run(_control_bot(factory, client, minutes_later=4).remind_unhandled_failures()) == 0
    )
    assert (
        asyncio.run(_control_bot(factory, client, minutes_later=6).remind_unhandled_failures()) == 1
    )
    assert (
        asyncio.run(_control_bot(factory, client, minutes_later=60).remind_unhandled_failures())
        == 0
    )

    reminder_text, reminder_buttons = client.sent[-1]
    assert reminder_text.startswith("⏰ 提醒")
    assert "停損單掛單失敗" in reminder_text
    assert reminder_buttons is not None


def test_control_bot_handled_button_stops_reminder(engine: Engine) -> None:
    factory = create_session_factory(engine)
    event_id = "e" * 64
    _append_execution_event(factory, "execution_unexpected_error", event_id)
    client = _RecordingBotClient()
    bot = _control_bot(factory, client)
    asyncio.run(bot.notify_execution_events())

    press = _CallbackEvent(encode_handled_callback(event_id))
    asyncio.run(bot._on_callback(press))
    press_again = _CallbackEvent(encode_handled_callback(event_id))
    asyncio.run(bot._on_callback(press_again))

    assert press.edits and press.edits[0][0].endswith("✅ 已標記為已處理")
    assert press.edits[0][1] is None
    assert press_again.answers == [("之前已經標記過了", False)]
    assert (
        asyncio.run(_control_bot(factory, client, minutes_later=6).remind_unhandled_failures()) == 0
    )


def test_control_bot_handled_button_rejects_unknown_event(engine: Engine) -> None:
    factory = create_session_factory(engine)
    press = _CallbackEvent(encode_handled_callback("f" * 64))

    asyncio.run(_control_bot(factory, _RecordingBotClient())._on_callback(press))

    assert press.answers == [("找不到這則通知", True)]
    assert press.edits == []


# --- Execution worker / position monitor (2026-09-29, explicit user request) ---


class _PositionClosedByStopTradingClient(_ProtectionFailsOnceTradingClient):
    """After a protected entry: the position is flat, the stop-loss algo triggered,
    the take-profit algo is still NEW and must be cancelled."""

    def __init__(self, *, flat: bool = True, cancel_error: Exception | None = None) -> None:
        super().__init__()
        self._flat = flat
        self._cancel_error = cancel_error
        self.cancelled: list[str] = []

    def get_position_risk(self, symbol: str | None = None) -> list[dict[str, object]]:
        return [{"symbol": "BTCUSDT", "positionAmt": "0" if self._flat else "1"}]

    def get_algo_order(self, *, client_algo_id: str) -> dict[str, object]:
        if client_algo_id.startswith("pr"):
            return {"algoStatus": "FINISHED", "actualOrderId": "901"}
        return {"algoStatus": "NEW", "actualOrderId": ""}

    def cancel_algo_order(self, *, client_algo_id: str) -> dict[str, object]:
        if self._cancel_error is not None:
            raise self._cancel_error
        self.cancelled.append(client_algo_id)
        return {"code": "200", "msg": "success"}

    def get_user_trades(self, symbol: str, *, start_time_ms: int) -> list[dict[str, object]]:
        return [
            {"side": "BUY", "qty": "1", "price": "100", "realizedPnl": "0", "commission": "0.04"},
            {"side": "SELL", "qty": "1", "price": "94", "realizedPnl": "-6", "commission": "0.04"},
        ]


def _open_protected_position(factory, channel_id: int) -> TradeIntentRow:  # type: ignore[no-untyped-def]
    """Approve, risk-evaluate and execute one BTCUSDT LONG, then add a resting
    take-profit row, leaving a PROTECTED position for the monitor to find."""
    _approve_signal(factory, channel_id)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )
    with factory() as session:
        ((trade_intent, risk_decision),) = load_eligible_intents(session)
    result = _execute_one(
        factory,
        trade_intent,
        risk_decision,
        settings=_risk_settings(),
        market_data_client=_FakeExecutionMarketDataClient(),
        trading_client=_ProtectionFailsOnceTradingClient(),
        clock=lambda: RISK_NOW,
        poll_timeout_seconds=0,
        poll_interval_seconds=0,
        protection_deadline_seconds=5,
    )
    assert result.final_state == "PROTECTED"
    with factory.begin() as session:
        session.add(
            ExchangeOrder(
                order_row_id=f"tp-row-{channel_id}",
                intent_id=trade_intent.intent_id,
                purpose="TAKE_PROFIT",
                client_order_id=f"tp-{channel_id}",
                symbol="BTCUSDT",
                side="SELL",
                order_type="TAKE_PROFIT_MARKET",
                reduce_only=True,
                status="NEW",
            )
        )
    return trade_intent


def _execution_events(factory, intent_id: str) -> dict[str, dict[str, object]]:  # type: ignore[no-untyped-def]
    with factory() as session:
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == intent_id)
        ).all()
    return {event.event_type: event.payload for event in events}


def test_position_monitor_reports_stop_loss_close_and_cancels_take_profit(engine: Engine) -> None:
    factory = create_session_factory(engine)
    trade_intent = _open_protected_position(factory, 840)
    client = _PositionClosedByStopTradingClient()

    results = run_position_monitor(factory, trading_client=client, clock=lambda: RISK_NOW)
    again = run_position_monitor(factory, trading_client=client, clock=lambda: RISK_NOW)

    assert [r.close_reason for r in results] == ["STOP_LOSS"]
    assert again == []  # CLOSED is terminal: never notified twice
    assert client.cancelled == ["tp-840"]
    closed = _execution_events(factory, trade_intent.intent_id)["execution_position_closed"]
    assert closed["close_reason"] == "停損觸發"
    assert closed["exit_price"] == "94"
    assert closed["realized_pnl"] == "-6"
    assert closed["cancelled_leftover"] == "停利單"
    with factory() as session:
        states = session.scalars(
            select(OrderLifecycle.state)
            .where(OrderLifecycle.intent_id == trade_intent.intent_id)
            .order_by(OrderLifecycle.revision)
        ).all()
        tp_status = session.scalar(
            select(ExchangeOrder.status).where(ExchangeOrder.client_order_id == "tp-840")
        )
    assert states[-1] == "CLOSED"
    assert tp_status == "CANCELED"


def test_position_monitor_leaves_open_position_alone(engine: Engine) -> None:
    factory = create_session_factory(engine)
    trade_intent = _open_protected_position(factory, 841)
    client = _PositionClosedByStopTradingClient(flat=False)

    assert run_position_monitor(factory, trading_client=client, clock=lambda: RISK_NOW) == []
    assert client.cancelled == []
    assert "execution_position_closed" not in _execution_events(factory, trade_intent.intent_id)


def test_position_monitor_notifies_when_leftover_cancel_fails(engine: Engine) -> None:
    factory = create_session_factory(engine)
    trade_intent = _open_protected_position(factory, 842)
    client = _PositionClosedByStopTradingClient(
        cancel_error=BinanceApiError(400, -1000, "cancel rejected")
    )

    run_position_monitor(factory, trading_client=client, clock=lambda: RISK_NOW)

    events = _execution_events(factory, trade_intent.intent_id)
    assert events["execution_position_closed"]["cancelled_leftover"] is None
    assert "cancel rejected" in str(events["execution_leftover_cancel_failed"]["orders"])


def test_risk_rejection_is_notified(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 843)

    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(None),
        clock=lambda: RISK_NOW,
    )

    with factory() as session:
        (event,) = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "execution_risk_rejected")
        ).all()
    assert event.payload["symbol"] == "BTCUSDT"
    assert event.payload["reason_codes"] != "-"


def test_worker_step_failure_is_notified_once_and_recovery_once(engine: Engine) -> None:
    factory = create_session_factory(engine)
    health = StepHealth()
    times = iter(RISK_NOW + timedelta(seconds=i) for i in range(10))

    for error in (RuntimeError("binance down"), RuntimeError("binance down"), None, None):
        health.record(factory, "execution", error, now=next(times))

    with factory() as session:
        events = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.aggregate_type == "execution_worker")
            .order_by(OutboxEvent.recorded_at)
        ).all()
    assert [e.event_type for e in events] == [
        "execution_worker_error",
        "execution_worker_recovered",
    ]
    assert "binance down" in events[0].payload["error"]


# ---------------------------------------------------------------- Web Console record queries


def test_console_signals_list_filters_by_decision_and_detail_shows_flow(engine: Engine) -> None:
    factory = create_session_factory(engine)
    request_id = _create_signal_and_request(factory, 960)
    with factory.begin() as session:
        signal = session.scalar(select(NormalizedSignalRow))
        assert signal is not None
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        create_edit(session, request, field="STOP", value=Decimal("90"))
    page = console_records.Page()

    with factory() as session:
        pending = console_records.list_signals(
            session, console_records.SignalFilter(decision="pending"), page
        )
        assert pending["total"] == 1
        assert pending["items"][0]["outcome"] is None
        assert pending["items"][0]["request_id"] == request_id

    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=555,
            callback_query_id="cbq-console",
            action="APPROVE",
            now=SIGNAL_FIXTURE_NOW,
        )

    with factory() as session:
        approved = console_records.list_signals(
            session, console_records.SignalFilter(decision="approved"), page
        )
        assert approved["total"] == 1
        assert approved["items"][0]["outcome"] == "APPROVED"
        assert approved["items"][0]["symbol"] == "BTCUSDT"
        rejected = console_records.list_signals(
            session, console_records.SignalFilter(decision="rejected"), page
        )
        assert rejected["total"] == 0
        assert console_records.parser_versions(session) == [signal.parser_version]

        detail = console_records.signal_detail(session, signal.signal_row_id)
        assert detail is not None
        assert detail["message"]["text"] == "BTC 多 市價進場"
        assert [edit["stop_value"] for edit in detail["edits"]] == ["90"]
        assert [d["outcome"] for d in detail["decisions"]] == ["APPROVED"]
        assert detail["decisions"][0]["approved_stop_value"] == "90"
        assert console_records.signal_detail(session, "missing") is None


def test_console_orders_and_open_positions(engine: Engine) -> None:
    factory = create_session_factory(engine)
    intent = _open_protected_position(factory, 961)
    page = console_records.Page()

    with factory() as session:
        orders = console_records.list_orders(session, console_records.OrderFilter(), page)
        assert orders["total"] == 1
        row = orders["items"][0]
        assert row["intent_id"] == intent.intent_id
        assert row["verdict"] == "APPROVED"
        assert row["state"] == "PROTECTED"
        assert row["entry_status"] == "FILLED"
        none_yet = console_records.list_orders(
            session, console_records.OrderFilter(state="NONE"), page
        )
        assert none_yet["total"] == 0

        detail = console_records.order_detail(session, intent.intent_id)
        assert detail is not None
        assert detail["risk_decision"]["verdict"] == "APPROVED"
        assert detail["lifecycle"][-1]["state"] == "PROTECTED"
        assert detail["protection"]["state"] == "CONFIRMED"
        assert {o["purpose"] for o in detail["exchange_orders"]} >= {"ENTRY", "PROTECTION"}
        assert any(e["event_type"] == "execution_entry_filled" for e in detail["events"])

        positions = console_records.list_open_positions(session, lambda _symbol: Decimal("110"))
        assert len(positions) == 1
        position = positions[0]
        assert position["symbol"] == "BTCUSDT"
        assert position["mark_price"] == "110"
        quantity = Decimal(position["quantity"])
        entry_price = Decimal(position["entry_price"])
        expected = ((Decimal("110") - entry_price) * quantity).quantize(Decimal("0.0001"))
        assert Decimal(position["unrealized_pnl"]) == expected

        unavailable = console_records.list_open_positions(session, lambda _symbol: None)
        assert unavailable[0]["unrealized_pnl"] is None


def test_console_theses_and_audit(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(_channel_policy_analysis(962))
    _seed_thesis_candidate(factory, 962)
    run_thesis_extraction(factory, _FakeOpenAiClient(), model="gpt-4o-mini")
    page = console_records.Page()

    with factory() as session:
        theses = console_records.list_theses(
            session, console_records.ThesisFilter(channel_id=962), page
        )
        assert theses["total"] == 1
        assert theses["items"][0]["primary_direction"] == "NEUTRAL"
        bearish = console_records.list_theses(
            session, console_records.ThesisFilter(direction="BEARISH"), page
        )
        assert bearish["total"] == 0
        detail = console_records.thesis_detail(session, theses["items"][0]["thesis_row_id"])
        assert detail is not None
        assert detail["source_text_included"] == _THESIS_SOURCE_TEXT
        assert detail["conditions"][0]["trigger_price"] == "773"

        facets = console_records.audit_facets(session)
        assert "telegram.message.persisted" in facets["event_types"]
        audit = console_records.list_audit(
            session,
            console_records.AuditFilter(event_type="telegram.message.persisted"),
            console_records.Page(limit=1),
        )
        assert audit["total"] >= 1
        assert len(audit["items"]) == 1
        future = console_records.list_audit(
            session,
            console_records.AuditFilter(since=datetime.now(UTC) + timedelta(days=1)),
            page,
        )
        assert future["total"] == 0


# ---------------------------------------------------------------- Two-month retention (0022)


def test_retention_cleanup_keeps_message_whose_signal_led_to_an_order(engine: Engine) -> None:
    factory = create_session_factory(engine)
    intent = _open_protected_position(factory, 970)
    with factory.begin() as session:
        session.execute(
            text("UPDATE channel_policies SET raw_retention_days = 1 WHERE channel_id = 970")
        )
    far_future = datetime.now(UTC) + timedelta(days=365)

    run_retention_cleanup(factory, MediaStore(Path("media")), now=lambda: far_future)

    with factory() as session:
        kept = session.get(TradeIntentRow, intent.intent_id)
        assert kept is not None
        signal = session.get(NormalizedSignalRow, kept.signal_row_id)
        assert signal is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramMessageVersion)
                .where(TelegramMessageVersion.source_event_id == signal.raw_message_id)
            )
            == 1
        )
        assert session.scalar(
            select(func.count())
            .select_from(OrderLifecycle)
            .where(OrderLifecycle.intent_id == intent.intent_id)
        )


def test_retention_migration_guard_sees_0022(engine: Engine) -> None:
    assert retention_migration_applied(create_session_factory(engine))


def test_trade_intent_with_orders_cannot_be_deleted(engine: Engine) -> None:
    factory = create_session_factory(engine)
    intent = _open_protected_position(factory, 971)

    with pytest.raises(DBAPIError, match="has order records"), factory.begin() as session:
        session.execute(
            text("DELETE FROM trade_intent WHERE intent_id = :id"), {"id": intent.intent_id}
        )


def test_retention_cleanup_deletes_risk_rejected_intent_without_orders(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _approve_signal(factory, 972)
    run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(None),
        clock=lambda: RISK_NOW,
    )
    with factory() as session:
        intent = session.scalar(select(TradeIntentRow).where(TradeIntentRow.channel_id == 972))
        assert intent is not None
        assert intent.status == "RISK_REJECTED"
    with factory.begin() as session:
        session.execute(
            text("UPDATE channel_policies SET raw_retention_days = 1 WHERE channel_id = 972")
        )

    run_retention_cleanup(
        factory, MediaStore(Path("media")), now=lambda: datetime.now(UTC) + timedelta(days=365)
    )

    with factory() as session:
        assert session.get(TradeIntentRow, intent.intent_id) is None


# ---------------------------------------------------------------- Realized PnL (0023)


def test_position_close_writes_realized_pnl_and_console_sums_it(engine: Engine) -> None:
    factory = create_session_factory(engine)
    trade_intent = _open_protected_position(factory, 980)
    page = console_records.Page()

    with factory() as session:
        before = console_records.overview_counts(
            session,
            failure_event_types=frozenset({"execution_protection_failed"}),
            handled_consumer="control_bot_handled",
            today_start=RISK_NOW - timedelta(hours=1),
            now=RISK_NOW,
        )
    assert before["open_positions"] == 1

    run_position_monitor(
        factory, trading_client=_PositionClosedByStopTradingClient(), clock=lambda: RISK_NOW
    )

    with factory() as session:
        row = session.get(RealizedPnl, trade_intent.intent_id)
        assert row is not None
        assert row.close_reason == "STOP_LOSS"
        assert row.exit_price == Decimal("94")
        assert row.quantity == Decimal("1")
        assert row.realized_pnl == Decimal("-6")
        assert row.exit_commission == Decimal("0.04")
        assert row.entry_price is not None
        assert row.channel_id == 980

        flt = console_records.PnlFilter(since=RISK_NOW - timedelta(days=1))
        summary = console_records.pnl_summary(session, flt)
        assert summary["trades"] == 1
        assert summary["losses"] == 1
        assert summary["wins"] == 0
        assert summary["win_rate"] == "0"
        assert summary["net_pnl"] == "-6.04"
        assert summary["fees"] == "0.04"

        daily = console_records.pnl_daily(session, flt, "Asia/Taipei")
        assert daily == [
            {"day": "2026-09-06", "trades": 1, "net_pnl": "-6.04", "wins": 0, "losses": 1}
        ]
        listed = console_records.list_pnl(
            session, console_records.PnlFilter(close_reason="STOP_LOSS"), page
        )
        assert listed["total"] == 1
        assert listed["items"][0]["net_pnl"] == "-6.04"
        assert (
            console_records.list_pnl(
                session, console_records.PnlFilter(close_reason="TAKE_PROFIT"), page
            )["total"]
            == 0
        )

        after = console_records.overview_counts(
            session,
            failure_event_types=frozenset({"execution_protection_retrying"}),
            handled_consumer="control_bot_handled",
            today_start=RISK_NOW - timedelta(hours=1),
            now=RISK_NOW,
        )
    assert after["open_positions"] == 0
    assert after["today"]["net_pnl"] == "-6.04"
    assert after["recent_closes"][0]["intent_id"] == trade_intent.intent_id
    # _ProtectionFailsOnceTradingClient's first stop attempt emits a retrying notice.
    assert after["unhandled_failures"] == 1
    assert after["unhandled_failure_events"][0]["event_type"] == "execution_protection_retrying"


def test_realized_pnl_is_append_only(engine: Engine) -> None:
    factory = create_session_factory(engine)
    trade_intent = _open_protected_position(factory, 981)
    run_position_monitor(
        factory, trading_client=_PositionClosedByStopTradingClient(), clock=lambda: RISK_NOW
    )

    with pytest.raises(DBAPIError, match="append-only"), factory.begin() as session:
        session.execute(
            text("UPDATE realized_pnl SET realized_pnl = 0 WHERE intent_id = :id"),
            {"id": trade_intent.intent_id},
        )


# ---------------------------------------------------------------- Daily loss kill switch (0024)


def _close_losing_position(factory, channel_id: int, limit: DailyLossLimit | None) -> None:  # type: ignore[no-untyped-def]
    """One BTCUSDT LONG closed by its stop at RISK_NOW: net -6.04 USDT."""
    _open_protected_position(factory, channel_id)
    run_position_monitor(
        factory,
        trading_client=_PositionClosedByStopTradingClient(),
        clock=lambda: RISK_NOW,
        daily_loss_limit=limit,
    )


def _button_texts(buttons: object) -> list[str]:
    assert isinstance(buttons, list)
    return [button.text for row in buttons for button in row]


def _daily_alert(factory: sessionmaker[Session]) -> OutboxEvent | None:
    with factory() as session:
        return session.get(OutboxEvent, alert_event_id(utc_day_start(RISK_NOW)))


def test_daily_loss_alert_is_raised_once_when_the_loss_reaches_the_limit(engine: Engine) -> None:
    factory = create_session_factory(engine)
    limit = DailyLossLimit(equity_usdt=Decimal("100"), threshold_pct=Decimal("0.06"))
    _close_losing_position(factory, 990, limit)

    alert = _daily_alert(factory)
    assert alert is not None
    assert alert.event_type == DAILY_LOSS_EVENT_TYPE
    assert alert.payload == {
        "day_start_utc": "2026-09-06T00:00:00+00:00",
        "net_pnl": "-6.04",
        "loss_pct": "6.04",
        "threshold_pct": "6",
        "equity_usdt": "100",
    }
    with factory.begin() as session:
        assert not alert_if_daily_loss_limit_reached(session, limit, RISK_NOW)


def test_daily_loss_alert_not_raised_below_limit_or_without_equity(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _close_losing_position(
        factory, 991, DailyLossLimit(equity_usdt=Decimal("1000"), threshold_pct=Decimal("0.06"))
    )
    assert _daily_alert(factory) is None
    with factory.begin() as session:
        assert not alert_if_daily_loss_limit_reached(
            session, DailyLossLimit(equity_usdt=None, threshold_pct=Decimal("0.06")), RISK_NOW
        )


def test_daily_loss_stop_button_pauses_until_next_utc_midnight(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _close_losing_position(
        factory, 992, DailyLossLimit(equity_usdt=Decimal("100"), threshold_pct=Decimal("0.06"))
    )
    alert = _daily_alert(factory)
    assert alert is not None
    client = _RecordingBotClient()
    asyncio.run(_control_bot(factory, client).notify_execution_events())
    alert_messages = [(text, buttons) for text, buttons in client.sent if "熔斷" in text]
    assert len(alert_messages) == 1
    assert _button_texts(alert_messages[0][1]) == ["⛔ 停止今日開單", "▶️ 繼續開單"]

    press = _CallbackEvent(encode_daily_loss_callback(alert.event_id, "STOP"))
    asyncio.run(_control_bot(factory, client)._on_callback(press))
    again = _CallbackEvent(encode_daily_loss_callback(alert.event_id, "CONTINUE"))
    asyncio.run(_control_bot(factory, client)._on_callback(again))

    assert "已停止今日開單" in press.edits[0][0]
    assert "09/07 08:00" in press.edits[0][0]
    assert again.answers[0][0] is not None and again.answers[0][0].startswith("之前已經選過了")
    with factory() as session:
        pause = active_pause(session, RISK_NOW)
        assert pause is not None
        assert pause.paused_until == datetime(2026, 9, 7, tzinfo=UTC)
        assert active_pause(session, datetime(2026, 9, 7, tzinfo=UTC)) is None
    # Answered, so no 5-minute reminder.
    assert (
        asyncio.run(_control_bot(factory, client, minutes_later=6).remind_unhandled_failures()) == 0
    )


def test_daily_loss_question_is_reminded_once_if_unanswered(engine: Engine) -> None:
    factory = create_session_factory(engine)
    _close_losing_position(
        factory, 993, DailyLossLimit(equity_usdt=Decimal("100"), threshold_pct=Decimal("0.06"))
    )
    client = _RecordingBotClient()
    asyncio.run(_control_bot(factory, client).notify_execution_events())
    client.sent.clear()

    assert (
        asyncio.run(_control_bot(factory, client, minutes_later=6).remind_unhandled_failures()) >= 1
    )
    reminders = [(text, buttons) for text, buttons in client.sent if "熔斷" in text]
    assert len(reminders) == 1
    assert _button_texts(reminders[0][1]) == ["⛔ 停止今日開單", "▶️ 繼續開單"]


def test_paused_trading_rejects_new_approvals_and_continue_does_not(engine: Engine) -> None:
    factory = create_session_factory(engine)
    day = utc_day_start(RISK_NOW)
    with factory.begin() as session:
        stop_alert = append_outbox_event(
            session,
            event_id=alert_event_id(day),
            event_type=DAILY_LOSS_EVENT_TYPE,
            aggregate_type="trading_control",
            aggregate_id=day.date().isoformat(),
            payload={"day_start_utc": day.isoformat()},
        )
        continue_alert = append_outbox_event(
            session,
            event_id="c" * 64,
            event_type=DAILY_LOSS_EVENT_TYPE,
            aggregate_type="trading_control",
            aggregate_id=day.date().isoformat(),
            payload={"day_start_utc": day.isoformat()},
        )
        session.flush()
        record_pause_decision(
            session, continue_alert, decision="CONTINUE", actor_user_id=555, now=RISK_NOW
        )
        assert active_pause(session, RISK_NOW) is None
        record_pause_decision(session, stop_alert, decision="STOP", actor_user_id=555, now=RISK_NOW)
    _approve_signal(factory, 994)

    results = run_risk_evaluation(
        factory,
        settings=_risk_settings(),
        market_data_client=_FakeRiskMarketDataClient(Decimal("100")),
        clock=lambda: RISK_NOW,
    )

    assert [(r.verdict, r.reason_codes) for r in results] == [("REJECTED", ["TRADING_PAUSED"])]


# ---------------------------------------------------------------- Web Console settings (0026)


def test_runtime_settings_save_apply_reset_and_history(engine: Engine) -> None:
    factory = create_session_factory(engine)
    base = Settings(risk_equity_baseline_usdt=Decimal("10000"))
    first = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)

    with factory.begin() as session:
        diffs = runtime_settings.record_changes(
            session,
            base,
            {
                "risk_leverage": "3",
                "risk_position_size_pct": "10",
                "risk_max_receive_lag_seconds": "10",
            },
            actor="admin",
            note="lower risk",
            now=first,
        )
    # The unchanged lag (10 = the .env value) is a no-op, not a row.
    assert [(d["key"], d["from"], d["to"]) for d in diffs] == [
        ("risk_leverage", "5", "3"),
        ("risk_position_size_pct", "20", "10"),
    ]

    effective = runtime_settings.load_effective_settings(factory, base)
    assert effective.risk_leverage == 3
    assert effective.risk_position_size_pct == Decimal("0.1")
    assert effective.risk_equity_baseline_usdt == Decimal("10000")

    with factory.begin() as session:
        runtime_settings.record_changes(
            session,
            base,
            {"risk_leverage": None},
            actor="admin",
            note=None,
            now=first + timedelta(minutes=1),
        )
    effective = runtime_settings.load_effective_settings(factory, base)
    assert effective.risk_leverage == 5
    assert effective.risk_position_size_pct == Decimal("0.1")

    with factory() as session:
        items = {i["key"]: i for i in runtime_settings.describe(session, base, "risk")}
        history = runtime_settings.history(session, "risk")
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "settings_changed")
        ).all()
    assert items["risk_position_size_pct"]["override"] == "10"
    assert items["risk_position_size_pct"]["env_value"] == "20"
    assert items["risk_leverage"]["override"] is None
    assert items["risk_leverage"]["effective"] == "5"
    rows = [(h["key"], h["from"], h["to"]) for h in history]
    assert rows[0] == ("risk_leverage", "3", None)  # newest first: the reset
    assert sorted(rows[1:], key=str) == sorted(
        [("risk_leverage", None, "3"), ("risk_position_size_pct", None, "10")], key=str
    )
    assert len(audit) == 2


def test_runtime_settings_reject_invalid_save_entirely(engine: Engine) -> None:
    factory = create_session_factory(engine)
    base = Settings()
    with (
        pytest.raises(runtime_settings.SettingValueError, match="必須介於"),
        factory.begin() as session,
    ):
        runtime_settings.record_changes(
            session,
            base,
            {"risk_leverage": "3", "risk_position_size_pct": "500"},
            actor="admin",
            note=None,
            now=datetime(2026, 10, 4, tzinfo=UTC),
        )
    assert runtime_settings.load_effective_settings(factory, base).risk_leverage == 5


def test_runtime_settings_ignore_a_stored_value_now_out_of_range(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            RuntimeSettingChange(
                change_id="x" * 64, key="risk_leverage", value="500", changed_by="old"
            )
        )
        session.add(
            RuntimeSettingChange(
                change_id="y" * 64, key="no_longer_editable", value="1", changed_by="old"
            )
        )
    assert runtime_settings.load_effective_settings(factory, Settings()).risk_leverage == 5


def test_runtime_setting_changes_are_append_only(engine: Engine) -> None:
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            RuntimeSettingChange(change_id="z" * 64, key="risk_leverage", value="4", changed_by="a")
        )
    with pytest.raises(DBAPIError, match="append-only"), factory.begin() as session:
        session.execute(text("DELETE FROM runtime_setting_changes"))


# ---------------------------------------------------------------- Channel admin (step 5)


def _channel_form(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "channel_id": "-100970000001",
        "topic_id": "0",
        "label": "Web added",
        "username": "",
        "channel_type": "ANALYSIS",
        "gate_decision": "MONITOR_ONLY",
        "access_authorization": "GRANTED",
        "automation_authorization": "GRANTED",
        "ai_authorization": "PENDING",
        "media_authorization": "UNKNOWN",
        "symbol_scope_mode": "BINANCE_USDM_ACTIVE_PERPETUAL",
        "allowed_symbols": "",
        "prohibited_symbols": "",
        "message_languages": "zh-TW",
        "supported_content_types": ["TEXT"],
        "raw_retention_days": "60",
        "acceptance_owner": "User",
    }
    values.update(overrides)
    return values


def test_channel_admin_create_update_remove_with_audit(engine: Engine) -> None:
    factory = create_session_factory(engine)
    now = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    with factory.begin() as session:
        created = channel_admin.create_channel(session, _channel_form(), actor="admin", now=now)
        assert (created.channel_id, created.topic_id) == (970000001, 0)
    with pytest.raises(channel_admin.ChannelValueError, match="已經存在"), factory.begin() as s:
        channel_admin.create_channel(s, _channel_form(), actor="admin", now=now)

    with factory.begin() as session:
        unchanged = channel_admin.update_channel(
            session, 970000001, 0, _channel_form(), actor="admin", now=now
        )
        changed = channel_admin.update_channel(
            session,
            970000001,
            0,
            _channel_form(ai_authorization="GRANTED", gate_decision="REJECTED"),
            actor="admin",
            now=now,
        )
    assert unchanged == []
    assert {(c["field"], c["from"], c["to"]) for c in changed} == {
        ("ai_authorization", "PENDING", "GRANTED"),
        ("gate_decision", "MONITOR_ONLY", "REJECTED"),
    }
    with pytest.raises(LookupError), factory.begin() as session:
        channel_admin.update_channel(session, 1, 0, _channel_form(), actor="admin", now=now)

    with factory() as session:
        rows = {r["channel_id"]: r for r in channel_admin.list_channel_policies(session)}
        audit = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.aggregate_type == "channel_policy")
            .order_by(AuditEvent.recorded_at)
        ).all()
    assert rows[970000001]["gate_decision"] == "REJECTED"
    assert rows[970000001]["last_message_at"] is None
    assert [a.event_type for a in audit] == ["channel_policy_created", "channel_policy_updated"]
    assert audit[1].payload["actor"] == "admin"


@pytest.mark.anyio
async def test_collector_picks_up_channel_added_from_web(engine: Engine) -> None:
    factory = create_session_factory(engine)
    configured = TelegramChannelTarget(channel_id=562, topic_id=None, username=None, label="Cfg")
    with factory.begin() as session:
        session.add(
            _channel_policy(
                channel_id=562,
                topic_id=0,
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
        channel_admin.create_channel(
            session,
            _channel_form(channel_id="970000002", topic_id="7", label="Web topic"),
            actor="admin",
            now=datetime(2026, 10, 5, tzinfo=UTC),
        )
        channel_admin.create_channel(
            session,
            _channel_form(channel_id="970000003", label="Paused", gate_decision="PAUSED"),
            actor="admin",
            now=datetime(2026, 10, 5, tzinfo=UTC),
        )

    with factory() as session:
        candidates = targets_with_policies(session, [configured])
    assert [(t.channel_id, t.topic_id, t.label) for t in candidates] == [
        (562, None, "Cfg"),
        (970000002, 7, "Web topic"),
        (970000003, None, "Paused"),
    ]

    class _Client:
        disconnects = 0

        async def disconnect(self) -> None:
            self.disconnects += 1

    class _Sink:
        expected: frozenset[tuple[int, int | None]] = frozenset()

        def set_expected_targets(self, expected: frozenset[tuple[int, int | None]]) -> None:
            self.expected = expected

    client, sink = _Client(), _Sink()
    collector = TelethonReadOnlyCollector(client, sink, targets=[configured])  # type: ignore[arg-type]

    removed = await collector.reload_policy_once(factory, [configured])

    assert removed == []
    assert [t.channel_id for t in collector._targets] == [562, 970000002]  # paused one skipped
    assert sink.expected == {(562, None), (970000002, 7)}
    assert client.disconnects == 1
    assert await collector.reload_policy_once(factory, [configured]) == []
    assert client.disconnects == 1  # already collecting: no second reconnect


def test_console_lists_every_take_profit_level_in_order(engine: Engine) -> None:
    factory = create_session_factory(engine)
    intent = _open_protected_position(factory, 999)
    with factory.begin() as session:
        session.execute(
            text("DELETE FROM take_profit_order WHERE intent_id = :id"), {"id": intent.intent_id}
        )
        for level, price, state in ((1, "130", "PENDING"), (0, "120", "CONFIRMED")):
            session.add(
                TakeProfitOrderRow(
                    take_profit_row_id=f"tp-level-{level}",
                    intent_id=intent.intent_id,
                    level_index=level,
                    target_quantity=Decimal("0.5"),
                    target_price=Decimal(price),
                    state=state,
                )
            )

    with factory() as session:
        detail = console_records.order_detail(session, intent.intent_id)
        positions = console_records.list_open_positions(session, lambda _symbol: None)
    assert detail is not None
    assert [(t["level_index"], t["target_price"], t["state"]) for t in detail["take_profits"]] == [
        (0, "120", "CONFIRMED"),
        (1, "130", "PENDING"),
    ]
    assert positions[0]["take_profits"] == [
        {"price": "120", "state": "CONFIRMED"},
        {"price": "130", "state": "PENDING"},
    ]
