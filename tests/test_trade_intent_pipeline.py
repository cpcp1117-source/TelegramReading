from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from telegram_trader.models import NormalizedSignal, TelegramMessageVersion
from telegram_trader.risk_engine import (
    REASON_DAILY_LOSS_KILL_SWITCH,
    REASON_SIGNAL_NOT_VALIDATED,
    RiskConfig,
)
from telegram_trader.trade_intent_pipeline import preview_trade_intent

NOW = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)


class _FakeMarketDataClient:
    def __init__(self, price: Decimal | None = Decimal("100")) -> None:
        self._price = price

    def get_mark_price(self, symbol: str) -> dict[str, str]:
        if self._price is None:
            return {}
        return {"markPrice": str(self._price)}

    def get_klines(self, symbol: str, *, start_time_ms: int, limit: int = 1) -> list[list[object]]:
        if self._price is None:
            return []
        return [
            [start_time_ms, str(self._price), str(self._price), str(self._price), str(self._price)]
        ]


def _config(**overrides: object) -> RiskConfig:
    values: dict[str, object] = {
        "leverage": 5,
        "default_stop_roe_pct": Decimal("0.30"),
        "position_size_pct": Decimal("0.20"),
        "max_concurrent_positions": 3,
        "daily_loss_kill_switch_pct": Decimal("0.06"),
        "max_receive_lag_seconds": 10,
        "max_price_deviation_bps": 50,
        "equity_baseline_usdt": Decimal("10000"),
    }
    values.update(overrides)
    return RiskConfig(**values)  # type: ignore[arg-type]


def _signal(**overrides: object) -> NormalizedSignal:
    values: dict[str, object] = {
        "signal_row_id": "row-1",
        "signal_id": "sig-1",
        "revision": 0,
        "raw_message_id": "raw-1",
        "normalizer_version": "v2",
        "channel_id": 2439599598,
        "topic_id": None,
        "status": "VALIDATED",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_type": "MARKET",
        "entry_values": [],
        "stop_value": Decimal("90"),
        "stop_origin": "AUTHOR",
        "take_profits": [],
        "evidence_spans": {},
        "link_method": None,
        "parser_version": "v1",
        "expires_at": NOW + timedelta(hours=24),
    }
    values.update(overrides)
    return NormalizedSignal(**values)


def _raw_message(**overrides: object) -> TelegramMessageVersion:
    values: dict[str, object] = {
        "source_event_id": "raw-1",
        "channel_id": 2439599598,
        "topic_id": None,
        "message_id": 1,
        "edit_version": 0,
        "event_kind": "NEW",
        "is_backfill": False,
        "source_date": NOW - timedelta(minutes=1),
        "received_at": NOW - timedelta(minutes=1) + timedelta(seconds=1),
        "text": "BTC LONG",
        "content_type": "text",
        "content_hash": "hash-1",
        "audit_event_id": "audit-1",
    }
    values.update(overrides)
    return TelegramMessageVersion(**values)


def test_preview_trade_intent_approves_and_sizes_flat() -> None:
    evaluation = preview_trade_intent(
        _signal(),
        _raw_message(),
        config=_config(),
        market_data_client=_FakeMarketDataClient(Decimal("100")),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    # equity=10000, position_size_pct=20% => margin=2000; 5x => notional=10000;
    # entry_price=100 (market) => quantity=100.
    assert evaluation.quantity == Decimal("100")
    assert evaluation.entry_price_used == Decimal("100")
    assert evaluation.computed_stop_price == Decimal("90")


def test_preview_trade_intent_rejects_when_signal_not_validated() -> None:
    evaluation = preview_trade_intent(
        _signal(status="NEW"),
        _raw_message(),
        config=_config(),
        market_data_client=_FakeMarketDataClient(),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert evaluation.reason_codes == [REASON_SIGNAL_NOT_VALIDATED]
    assert evaluation.quantity is None


def test_preview_trade_intent_handles_missing_market_price_without_raising() -> None:
    evaluation = preview_trade_intent(
        _signal(),
        _raw_message(),
        config=_config(),
        market_data_client=_FakeMarketDataClient(price=None),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert evaluation.quantity is None


def test_preview_trade_intent_advisory_does_not_block_approval() -> None:
    """BR-011 (kill switch) etc. are only checked against `AccountState` inside

    `evaluate_trade_intent` itself -- `preview_trade_intent` always starts
    from a clean-slate account (Slice-1 limitation), so this mainly proves
    the preview path doesn't accidentally regress that already-tested
    advisory behavior.
    """
    evaluation = preview_trade_intent(
        _signal(),
        _raw_message(),
        config=_config(daily_loss_kill_switch_pct=Decimal("0.00")),
        market_data_client=_FakeMarketDataClient(Decimal("100")),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert REASON_DAILY_LOSS_KILL_SWITCH in evaluation.advisory_codes
