"""Entry-price / position-size draft edits, end to end through approval and the Risk Engine.

Real PostgreSQL, fake market data: the values the user edits before approving must reach the
`trade_intent` (entry) and the `risk_decision` (quantity), and an untouched draft must start from
the signal's own suggested size.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, text

from telegram_trader.config import Settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.draft_edit import EntryEdit
from telegram_trader.models import (
    ChannelPolicy,
    NormalizedContent,
    NormalizedSignal,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TelegramMessageVersion,
)
from telegram_trader.models import RiskDecision as RiskDecisionRow
from telegram_trader.models import TradeIntent as TradeIntentRow
from telegram_trader.normalize_content import run_normalization
from telegram_trader.parse_signals import run_signal_parsing
from telegram_trader.signal_decisions import (
    DraftValues,
    create_edit,
    create_request,
    load_current_draft,
    record_decision,
    request_id_for_signal_row,
)
from telegram_trader.telegram_storage import (
    MediaStore,
    TelegramMessageInput,
    TelegramMessageProcessor,
)
from telegram_trader.trade_intent_pipeline import (
    ADVISORY_ENTRY_FAR_FROM_MARKET,
    preview_trade_intent,
    risk_config_from_settings,
    run_risk_evaluation,
)

pytestmark = pytest.mark.integration

CHANNEL = 850
NOW = datetime(2026, 9, 6, 8, 0, 30, tzinfo=UTC)
POSTED = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


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
                "normalized_signals, normalized_content, telegram_message_versions, "
                "trading_pause_decisions, risk_config_snapshot, audit_events, "
                "channel_policies, signal_parse_checkpoints "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield active_engine
    active_engine.dispose()


class FakeMarket:
    def __init__(self, price: str = "100") -> None:
        self._price = price

    def get_mark_price(self, symbol: str) -> dict[str, str]:
        return {"markPrice": self._price}

    def get_klines(self, symbol: str, *, start_time_ms: int, limit: int = 1) -> list[list[Any]]:
        return [[start_time_ms, self._price, self._price, self._price, self._price]]


def _seed_signal(
    factory: Any, tmp_path: Path, *, ingest_lag: timedelta = timedelta(0), **overrides: Any
) -> tuple[str, str]:
    """A VALIDATED BTCUSDT LONG signal plus its raw message; returns `(signal_row_id, nonce)`.

    `ingest_lag` is how long after the post the collector received the message (a backfilled
    message has a large one).
    """
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(CHANNEL, None)})
    )
    result = processor.process(
        TelegramMessageInput(
            channel_id=CHANNEL,
            message_id=1,
            event_kind="NEW",
            source_date=POSTED,
            received_at=POSTED + ingest_lag,
            text="BTC long",
            content_type="text",
        )
    )
    values: dict[str, Any] = {
        "signal_row_id": "row-1",
        "signal_id": "sig-1",
        "revision": 0,
        "raw_message_id": result.source_event_id,
        "normalizer_version": "n1",
        "channel_id": CHANNEL,
        "topic_id": None,
        "status": "VALIDATED",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_type": "MARKET",
        "entry_values": [],
        "stop_value": Decimal("90"),
        "stop_origin": "AUTHOR",
        "take_profits": ["120"],
        "evidence_spans": {},
        "link_method": None,
        "parser_version": "t1",
        "expires_at": datetime(2026, 9, 7, tzinfo=UTC),
    }
    values.update(overrides)
    with factory.begin() as session:
        signal = NormalizedSignal(**values)
        session.add(signal)
        session.flush()
        create_request(session, signal, nonce="nonce-1", telegram_message_id=42)
    return values["signal_row_id"], "nonce-1"


def _approve(factory: Any, signal_row_id: str) -> None:
    with factory.begin() as session:
        outcome = record_decision(
            session,
            request_id=request_id_for_signal_row(signal_row_id),
            actor_user_id=555,
            callback_query_id="cbq-1",
            action="APPROVE",
            now=NOW,
        )
    assert outcome == "APPROVED"


def _settings() -> Settings:
    return Settings(risk_equity_baseline_usdt=Decimal("10000"))


def test_edited_entry_and_size_reach_intent_and_quantity(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    row_id, _ = _seed_signal(factory, tmp_path)
    request_id = request_id_for_signal_row(row_id)
    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id)
        assert request is not None
        create_edit(session, request, field="ENTRY", value=EntryEdit("LIMIT", Decimal("99")))
        draft = create_edit(session, request, field="SIZE", value=Decimal("0.05"))
    assert (draft.entry_type, draft.entry_value, draft.position_size_pct) == (
        "LIMIT",
        Decimal("99"),
        Decimal("0.05"),
    )
    assert draft.stop_value == Decimal("90")  # earlier fields are carried forward

    _approve(factory, row_id)
    [result] = run_risk_evaluation(
        factory, settings=_settings(), market_data_client=FakeMarket("100"), clock=lambda: NOW
    )

    assert result.verdict == "APPROVED"
    with factory() as session:
        event = session.scalars(select(SignalDecisionEvent)).one()
        assert (event.approved_entry_type, event.approved_entry_value) == ("LIMIT", Decimal("99"))
        assert event.approved_position_size_pct == Decimal("0.05")
        intent = session.scalars(select(TradeIntentRow)).one()
        assert intent.entry_type == "LIMIT" and [Decimal(v) for v in intent.entry_values] == [
            Decimal("99")
        ]
        decision = session.scalars(select(RiskDecisionRow)).one()
        assert decision.entry_price_used == Decimal("99")
        # margin = equity 10000 * 5% = 500; notional = 500 * 5x leverage; quantity = notional / 99
        assert decision.quantity == (Decimal("2500") / Decimal("99")).quantize(Decimal("1e-8"))


def test_untouched_draft_uses_the_signals_suggested_size(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    row_id, _ = _seed_signal(factory, tmp_path, suggested_size_pct=Decimal("0.10"))

    _approve(factory, row_id)
    run_risk_evaluation(
        factory, settings=_settings(), market_data_client=FakeMarket("100"), clock=lambda: NOW
    )

    with factory() as session:
        event = session.scalars(select(SignalDecisionEvent)).one()
        assert event.approved_position_size_pct == Decimal("0.1000")
        assert event.approved_entry_type is None  # the signal's own entry is untouched
        decision = session.scalars(select(RiskDecisionRow)).one()
        assert decision.quantity == Decimal("5000") / Decimal("100")  # 10% margin, 5x, price 100


def test_market_override_replaces_a_limit_entry(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    row_id, _ = _seed_signal(
        factory, tmp_path, entry_type="LIMIT", entry_values=["95"], stop_value=Decimal("90")
    )
    with factory.begin() as session:
        request = session.get(SignalDecisionRequest, request_id_for_signal_row(row_id))
        assert request is not None
        create_edit(session, request, field="ENTRY", value=EntryEdit("MARKET", None))

    _approve(factory, row_id)
    run_risk_evaluation(
        factory, settings=_settings(), market_data_client=FakeMarket("100"), clock=lambda: NOW
    )

    with factory() as session:
        intent = session.scalars(select(TradeIntentRow)).one()
        assert intent.entry_type == "MARKET" and intent.entry_values == []
        assert session.scalars(select(RiskDecisionRow)).one().entry_price_used == Decimal("100")


def test_preview_follows_the_draft_and_flags_a_far_limit_price(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    row_id, _ = _seed_signal(factory, tmp_path)
    config = risk_config_from_settings(_settings())
    with factory() as session:
        signal = session.get(NormalizedSignal, row_id)
        assert signal is not None
        raw = session.get(TelegramMessageVersion, signal.raw_message_id)
        assert raw is not None
        near = preview_trade_intent(
            signal,
            raw,
            config=config,
            market_data_client=FakeMarket("100"),
            now=NOW,
            draft=DraftValues(
                stop_value=Decimal("90"),
                take_profits=[],
                entry_type="LIMIT",
                entry_value=Decimal("99"),
                position_size_pct=Decimal("0.05"),
            ),
        )
        far = preview_trade_intent(
            signal,
            raw,
            config=config,
            market_data_client=FakeMarket("100"),
            now=NOW,
            draft=DraftValues(
                stop_value=Decimal("90"),
                take_profits=[],
                entry_type="LIMIT",
                entry_value=Decimal("120"),
            ),
        )

    assert near.quantity == Decimal("2500") / Decimal("99")
    assert ADVISORY_ENTRY_FAR_FROM_MARKET not in near.advisory_codes
    assert ADVISORY_ENTRY_FAR_FROM_MARKET in far.advisory_codes


def test_load_current_draft_without_edits_starts_from_signal(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    row_id, _ = _seed_signal(factory, tmp_path, suggested_size_pct=Decimal("0.10"))
    with factory() as session:
        request = session.get(SignalDecisionRequest, request_id_for_signal_row(row_id))
        assert request is not None
        draft = load_current_draft(session, request)

    assert draft.stop_value == Decimal("90")
    assert draft.take_profits == [Decimal("120")]
    assert draft.position_size_pct == Decimal("0.1000")
    assert draft.entry_type is None


def test_receive_lag_is_checked_for_live_signals_but_not_monitor_built_drafts(
    engine: Engine, tmp_path: Path
) -> None:
    """User decision 2026-10-04: a draft raised by the setup monitor skips the receive-lag check."""
    lag = timedelta(hours=3)  # e.g. the source message was backfilled long after it was posted

    factory = create_session_factory(engine)
    live_id, _ = _seed_signal(factory, tmp_path, ingest_lag=lag)
    _approve(factory, live_id)
    [live] = run_risk_evaluation(
        factory, settings=_settings(), market_data_client=FakeMarket("100"), clock=lambda: NOW
    )
    assert live.verdict == "REJECTED"
    assert "RECEIVE_LAG_TOO_HIGH" in live.reason_codes

    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE signal_decision_edits, signal_decision_events, "
                "signal_decision_requests, normalized_signals, normalized_content, "
                "telegram_message_versions, risk_config_snapshot, audit_events, outbox_events "
                "RESTART IDENTITY CASCADE"
            )
        )
    draft_id, _ = _seed_signal(factory, tmp_path, ingest_lag=lag, parser_version="setup-abc123")
    _approve(factory, draft_id)
    [draft] = run_risk_evaluation(
        factory, settings=_settings(), market_data_client=FakeMarket("100"), clock=lambda: NOW
    )
    assert draft.verdict == "APPROVED"


def test_risk_flagged_signal_gets_the_smaller_suggested_size(
    engine: Engine, tmp_path: Path
) -> None:
    """A signal the author flags as risky ("風險") starts at the smaller size; others do not."""
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            ChannelPolicy(
                channel_id=CHANNEL,
                topic_id=0,
                label="exec",
                channel_type="EXECUTION_SIGNAL",
                symbol_scope_mode="STATIC_ALLOWLIST",
                allowed_symbols=["BTCUSDT"],
                automation_authorization="GRANTED",
                gate_decision="ENABLED",
            )
        )
    processor = TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(CHANNEL, None)})
    )
    for message_id, body in ((1, "BTC 風險 多 市價進場"), (2, "BTC 多 市價進場")):
        processor.process(
            TelegramMessageInput(
                channel_id=CHANNEL,
                message_id=message_id,
                event_kind="NEW",
                source_date=POSTED,
                received_at=POSTED,
                text=body,
                content_type="text",
            )
        )
    run_normalization(factory)

    run_signal_parsing(factory, risk_flagged_size_pct=Decimal("0.05"))

    with factory() as session:
        sizes = {
            row.raw_message_id: row.suggested_size_pct
            for row in session.scalars(select(NormalizedSignal))
        }
        texts = {
            row.raw_message_id: row.normalized_text
            for row in session.scalars(select(NormalizedContent))
        }
    by_text = {texts[raw_id]: size for raw_id, size in sizes.items()}
    assert by_text["BTC 風險 多 市價進場"] == Decimal("0.0500")
    assert by_text["BTC 多 市價進場"] is None
