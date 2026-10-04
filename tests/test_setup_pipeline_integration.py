"""End-to-end test of the analysis-setup pipeline against a migrated PostgreSQL database.

Real database, fake AI client and fake Binance candles: an analyst message (plus the earlier
message it updates) is normalized, extracted into a setup, replayed against candles, and ends as a
`VALIDATED` signal the existing approval path can pick up.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, text

from telegram_trader.config import Settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.models import (
    AnalysisSetup,
    BinanceSymbolSnapshot,
    ChannelPolicy,
    NormalizedSignal,
    OutboxEvent,
)
from telegram_trader.normalize_content import run_normalization
from telegram_trader.openai_client import OpenAiClientError
from telegram_trader.setup_extraction import run_setup_extraction
from telegram_trader.setup_monitor import run_setup_monitor
from telegram_trader.signal_decisions import load_pending_signals
from telegram_trader.telegram_storage import (
    MediaStore,
    TelegramMessageInput,
    TelegramMessageProcessor,
)

pytestmark = pytest.mark.integration

CHANNEL = 3182714504
TOPIC = 7
T0 = datetime(2026, 10, 3, 4, 11, tzinfo=UTC)
FIRST = "DOT 空\nEN：1.18-1.2\nSL：1.23上方\nTP：1.08-1.1、1.03-1.05\n（如果先下跌接近目標就撤單）"  # noqa: RUF001
UPDATE = "小級別則是跌破1.18會破壞反彈的上升結構\n止損建議設在1.23以上\n（個人擺在1.25）"  # noqa: RUF001
MODEL = "test-model"


@pytest.fixture
def engine() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    integration_settings = Settings(database_url=database_url) if database_url else Settings()
    active_engine = create_db_engine(integration_settings)
    database_name = active_engine.url.database or ""
    if not database_name.endswith("_test"):
        active_engine.dispose()
        pytest.fail(f"refusing to TRUNCATE database {database_name!r}: must end with '_test'")
    with active_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE outbox_delivery_receipts, outbox_events, "
                "signal_decision_edits, signal_decision_events, signal_decision_requests, "
                "normalized_signals, signal_parse_checkpoints, analysis_setups, "
                "analysis_setup_checkpoints, thesis, thesis_extraction_checkpoints, "
                "normalized_content, binance_symbol_snapshots, "
                "telegram_message_versions, telegram_collector_checkpoints, "
                "consumer_checkpoints, audit_events, channel_policies "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield active_engine
    active_engine.dispose()


class FakeAi:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def extract_structured(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs["user_content"])
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeMarket:
    def __init__(self, rows: list[list[Any]]) -> None:
        self._rows = rows

    def get_candles(self, symbol: str, **_: Any) -> list[list[Any]]:
        return self._rows


def _kline(start: datetime, o: str, h: str, low: str, c: str) -> list[Any]:
    start_ms = int(start.timestamp() * 1000)
    return [start_ms, o, h, low, c, "1", start_ms + 15 * 60 * 1000 - 1]


def _setup_json(**overrides: Any) -> dict[str, Any]:
    setup: dict[str, Any] = {
        "symbol": "DOTUSDT",
        "side": "SHORT",
        "is_update_of_earlier_message": True,
        "entry_kind": "ZONE",
        "entry_zone_low": 1.18,
        "entry_zone_high": 1.2,
        "trigger_type": "NONE",
        "trigger_price": None,
        "stop_loss": 1.25,
        "take_profits": [{"low": 1.08, "high": 1.1}, {"low": 1.03, "high": 1.05}],
        "cancel_if_target_reached_first": True,
        "confidence": "MEDIUM",
        "risk_notes": [],
        "evidence_quotes": ["1.18-1.2"],
    }
    setup.update(overrides)
    return {"summary_zh": "DOT 偏空", "setups": [setup]}


def _seed(factory: Any, tmp_path: Path, *, now: datetime) -> None:
    with factory.begin() as session:
        session.add(
            ChannelPolicy(
                channel_id=CHANNEL,
                topic_id=TOPIC,
                label="vip",
                channel_type="ANALYSIS",
                access_authorization="GRANTED",
                automation_authorization="GRANTED",
                ai_authorization="GRANTED",
                media_authorization="GRANTED",
                symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL",
                allowed_symbols=[],
                prohibited_symbols=[],
                message_languages=["zh-TW"],
                supported_content_types=["TEXT", "CAPTION"],
                gate_decision="MONITOR_ONLY",
            )
        )
        session.add(
            BinanceSymbolSnapshot(
                snapshot_id="snap",
                fetched_at=now,
                active_symbols=["DOTUSDT", "BTCUSDT"],
                symbol_count=2,
            )
        )
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(CHANNEL, TOPIC)})
    )
    for message_id, when, body in ((1, T0, FIRST), (2, T0 + timedelta(hours=12), UPDATE)):
        processor.process(
            TelegramMessageInput(
                channel_id=CHANNEL,
                topic_id=TOPIC,
                message_id=message_id,
                event_kind="NEW",
                source_date=when,
                received_at=when,
                text=body,
                content_type="text",
            )
        )
    run_normalization(factory)


def test_message_to_setup_to_signal(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    now = T0 + timedelta(hours=14)
    _seed(factory, tmp_path, now=now)
    # Message 1 yields the original setup; message 2 (an update without the coin) replaces it.
    ai = FakeAi([_setup_json(stop_loss=1.23), _setup_json()])

    [result] = run_setup_extraction(factory, ai, model=MODEL, clock=lambda: now)

    assert (result.processed_count, result.created_count, result.failed_count) == (2, 2, 0)
    assert "【較早訊息" not in ai.calls[0]
    assert "DOT 空" in ai.calls[1] and "【最新訊息】" in ai.calls[1]  # update sees its context
    with factory() as session:
        rows = list(session.scalars(select(AnalysisSetup).order_by(AnalysisSetup.valid_from)))
        assert [r.status for r in rows] == ["SUPERSEDED", "WATCHING"]
        active = rows[1]
        assert active.stop_value is not None and str(active.stop_value) == "1.25000000"
        assert active.valid_from == T0 + timedelta(hours=12)
        created = list(
            session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "setup_created"))
        )
        assert len(created) == 2

    # Re-running does nothing: the cursor and deterministic ids make extraction idempotent.
    again = run_setup_extraction(factory, FakeAi([]), model=MODEL, clock=lambda: now)
    assert again[0].processed_count == 0

    after_update = T0 + timedelta(hours=12)
    market = FakeMarket(
        [
            _kline(after_update + timedelta(minutes=30), "1.17", "1.19", "1.17", "1.185"),
            _kline(after_update + timedelta(minutes=45), "1.19", "1.195", "1.175", "1.1811"),
        ]
    )

    monitored = run_setup_monitor(factory, market, clock=lambda: now)

    assert (monitored.touched_count, monitored.triggered_count, monitored.error_count) == (1, 1, 0)
    with factory() as session:
        setup = session.scalars(
            select(AnalysisSetup).where(AnalysisSetup.status == "TRIGGERED")
        ).one()
        assert setup.signal_id is not None
        signal = session.scalars(select(NormalizedSignal)).one()
        assert (signal.status, signal.side, signal.entry_type) == ("VALIDATED", "SHORT", "LIMIT")
        assert signal.entry_values == ["1.1811"]
        assert signal.take_profits == ["1.1", "1.05"]  # edge nearest entry
        assert signal.stop_origin == "AUTHOR"
        event_types = set(session.scalars(select(OutboxEvent.event_type)))
        assert {"setup_zone_touched", "setup_triggered"} <= event_types
        # The existing Control Bot loader sees it as a pending draft.
        assert [s.signal_id for s in load_pending_signals(session, now=now)] == [signal.signal_id]

    # Idempotent: a triggered setup is no longer monitored.
    assert run_setup_monitor(factory, market, clock=lambda: now).checked_count == 0


def test_ai_failure_does_not_advance_cursor_and_notifies_once(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    now = T0 + timedelta(hours=14)
    _seed(factory, tmp_path, now=now)

    failing = FakeAi([OpenAiClientError("codex CLI exited 1: login expired")])
    [first] = run_setup_extraction(factory, failing, model=MODEL, clock=lambda: now)
    [second] = run_setup_extraction(
        factory, FakeAi([OpenAiClientError("again")]), model=MODEL, clock=lambda: now
    )

    assert (first.failed_count, first.created_count) == (1, 0)
    assert second.failed_count == 1  # retried, not skipped past
    with factory() as session:
        failures = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "setup_extraction_failed")
            )
        )
        assert len(failures) == 1  # one notice per message, not one per retry

    recovered = FakeAi([_setup_json(stop_loss=1.23), _setup_json()])
    [third] = run_setup_extraction(factory, recovered, model=MODEL, clock=lambda: now)
    assert third.created_count == 2


def test_setup_expires_without_candles(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    start = T0 + timedelta(hours=14)
    _seed(factory, tmp_path, now=start)
    run_setup_extraction(
        factory,
        FakeAi([_setup_json(stop_loss=1.23), _setup_json()]),
        model=MODEL,
        clock=lambda: start,
    )

    late = T0 + timedelta(hours=12 + 49)
    result = run_setup_monitor(factory, FakeMarket([]), clock=lambda: late)

    assert result.closed_count == 1
    with factory() as session:
        statuses = {r.status for r in session.scalars(select(AnalysisSetup))}
        assert statuses == {"SUPERSEDED", "EXPIRED"}
        types = set(session.scalars(select(OutboxEvent.event_type)))
        assert "setup_expired" in types
