from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from telegram_trader.risk_engine import (
    REASON_DAILY_LOSS_KILL_SWITCH,
    REASON_INVALID_STOP,
    REASON_MAX_POSITIONS_REACHED,
    REASON_QUANTITY_NOT_POSITIVE,
    REASON_RECEIVE_LAG_TOO_HIGH,
    REASON_SOURCE_TOO_OLD,
    REASON_STALE_MARKET_DATA,
    REASON_SYMBOL_CONFLICT,
    AccountState,
    RiskConfig,
    TradeIntentInput,
    compute_roe_stop_price,
    evaluate_trade_intent,
)

NOW = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _config(**overrides: object) -> RiskConfig:
    values: dict[str, object] = {
        "leverage": 5,
        "default_stop_roe_pct": Decimal("0.30"),
        "max_single_trade_risk_pct": Decimal("0.03"),
        "max_single_trade_initial_margin_pct": Decimal("0.10"),
        "max_total_initial_margin_pct": Decimal("0.30"),
        "max_concurrent_positions": 3,
        "daily_loss_kill_switch_pct": Decimal("0.06"),
        "max_source_age_seconds": 60,
        "max_receive_lag_seconds": 10,
        "max_price_deviation_bps": 50,
        "equity_baseline_usdt": Decimal("10000"),
    }
    values.update(overrides)
    return RiskConfig(**values)  # type: ignore[arg-type]


def _account(**overrides: object) -> AccountState:
    values: dict[str, object] = {
        "equity_usdt": Decimal("10000"),
        "open_position_count": 0,
        "open_position_sides": {},
        "total_initial_margin_used_usdt": Decimal("0"),
        "daily_realized_loss_pct": Decimal("0"),
    }
    values.update(overrides)
    return AccountState(**values)  # type: ignore[arg-type]


def _intent(**overrides: object) -> TradeIntentInput:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_type": "MARKET",
        "entry_values": [],
        "stop_value": None,
        "signal_source_date": NOW - timedelta(seconds=5),
        "signal_received_at": NOW - timedelta(seconds=4),
    }
    values.update(overrides)
    return TradeIntentInput(**values)  # type: ignore[arg-type]


# --- compute_roe_stop_price (TBD-003 baseline formula) ---


def test_compute_roe_stop_price_long_is_below_entry() -> None:
    stop = compute_roe_stop_price(Decimal("100"), "LONG", leverage=5, roe_pct=Decimal("0.30"))
    assert stop == Decimal("94.0")


def test_compute_roe_stop_price_short_is_above_entry() -> None:
    stop = compute_roe_stop_price(Decimal("100"), "SHORT", leverage=5, roe_pct=Decimal("0.30"))
    assert stop == Decimal("106.0")


# --- golden path: approval ---


def test_evaluate_trade_intent_approves_clean_long_market_entry() -> None:
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", entry_type="MARKET"),
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.reason_codes == []
    assert evaluation.entry_price_used == Decimal("100")
    assert evaluation.computed_stop_price == Decimal("94.0")  # DEFAULT_ROE_30 baseline
    assert evaluation.quantity is not None
    assert evaluation.quantity > 0


def test_evaluate_trade_intent_uses_author_stop_when_present() -> None:
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", entry_type="MARKET", stop_value=Decimal("90")),
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.computed_stop_price == Decimal("90")


def test_evaluate_trade_intent_limit_entry_uses_signal_price_not_market_price() -> None:
    evaluation = evaluate_trade_intent(
        _intent(
            side="LONG",
            entry_type="LIMIT",
            entry_values=[Decimal("95")],
            stop_value=Decimal("90"),
        ),
        config=_config(),
        account=_account(),
        market_price=Decimal("200"),  # far from the limit price -- must not be used
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.entry_price_used == Decimal("95")


def test_evaluate_trade_intent_range_entry_uses_midpoint() -> None:
    evaluation = evaluate_trade_intent(
        _intent(
            side="LONG",
            entry_type="RANGE",
            entry_values=[Decimal("90"), Decimal("100")],
            stop_value=Decimal("80"),
        ),
        config=_config(),
        account=_account(),
        market_price=Decimal("200"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.entry_price_used == Decimal("95")


# --- BR-012: fail closed on missing market data ---


def test_evaluate_trade_intent_rejects_when_market_price_missing() -> None:
    evaluation = evaluate_trade_intent(
        _intent(),
        config=_config(),
        account=_account(),
        market_price=None,
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert evaluation.reason_codes == [REASON_STALE_MARKET_DATA]
    assert evaluation.quantity is None


# --- BR-011: daily loss kill switch ---


def test_evaluate_trade_intent_rejects_when_daily_kill_switch_triggered() -> None:
    evaluation = evaluate_trade_intent(
        _intent(),
        config=_config(),
        account=_account(daily_realized_loss_pct=Decimal("-0.06")),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert evaluation.reason_codes == [REASON_DAILY_LOSS_KILL_SWITCH]


def test_evaluate_trade_intent_allows_loss_just_under_kill_switch() -> None:
    evaluation = evaluate_trade_intent(
        _intent(),
        config=_config(),
        account=_account(daily_realized_loss_pct=Decimal("-0.0599")),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"


# --- BR-006: freshness ---


def test_evaluate_trade_intent_rejects_stale_source() -> None:
    evaluation = evaluate_trade_intent(
        _intent(
            signal_source_date=NOW - timedelta(seconds=61),
            signal_received_at=NOW - timedelta(seconds=60),
        ),
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_SOURCE_TOO_OLD in evaluation.reason_codes


def test_evaluate_trade_intent_rejects_high_receive_lag() -> None:
    evaluation = evaluate_trade_intent(
        _intent(
            signal_source_date=NOW - timedelta(seconds=20),
            signal_received_at=NOW - timedelta(seconds=8),
        ),
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_RECEIVE_LAG_TOO_HIGH in evaluation.reason_codes


# --- BR-007: symbol conflict ---


def test_evaluate_trade_intent_rejects_opposite_side_conflict() -> None:
    evaluation = evaluate_trade_intent(
        _intent(symbol="BTCUSDT", side="LONG"),
        config=_config(),
        account=_account(open_position_sides={"BTCUSDT": "SHORT"}, open_position_count=1),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_SYMBOL_CONFLICT in evaluation.reason_codes


def test_evaluate_trade_intent_allows_same_side_same_symbol() -> None:
    evaluation = evaluate_trade_intent(
        _intent(symbol="BTCUSDT", side="LONG"),
        config=_config(),
        account=_account(open_position_sides={"BTCUSDT": "LONG"}, open_position_count=1),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"


# --- BR-010: max concurrent positions ---


def test_evaluate_trade_intent_rejects_when_max_positions_reached() -> None:
    evaluation = evaluate_trade_intent(
        _intent(symbol="ETHUSDT"),
        config=_config(max_concurrent_positions=3),
        account=_account(open_position_count=3),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_MAX_POSITIONS_REACHED in evaluation.reason_codes


# --- Stop-side validation ---


def test_evaluate_trade_intent_rejects_stop_on_wrong_side_for_long() -> None:
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", stop_value=Decimal("110")),  # above entry, invalid for LONG
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_INVALID_STOP in evaluation.reason_codes


def test_evaluate_trade_intent_rejects_stop_on_wrong_side_for_short() -> None:
    evaluation = evaluate_trade_intent(
        _intent(side="SHORT", stop_value=Decimal("90")),  # below entry, invalid for SHORT
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_INVALID_STOP in evaluation.reason_codes


# --- BR-009: sizing caps ---


def test_evaluate_trade_intent_sizes_to_risk_cap_when_stop_is_far() -> None:
    # equity=10000, risk_pct=3% => risk_amount=300; stop distance = 100-90=10
    # risk-capped qty = 300/10 = 30, tighter than the single-margin cap's 50
    # (1000 margin cap * 5x / 100 price) and the 150 total-margin headroom.
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", stop_value=Decimal("90")),
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.quantity == Decimal("30")


def test_evaluate_trade_intent_sizing_capped_by_single_trade_margin_when_stop_is_close() -> None:
    # A close stop makes the risk-based quantity large (300/3=100); the 10%
    # single-trade margin cap must bind instead: margin_cap_usdt=1000,
    # qty = 1000*5/100 = 50, tighter than both the risk cap and 150 headroom.
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", stop_value=Decimal("97")),
        config=_config(),
        account=_account(),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.quantity == Decimal("50")


def test_evaluate_trade_intent_sizing_capped_by_remaining_total_margin() -> None:
    # Total margin cap is 30% of equity = 3000; 2900 already used elsewhere,
    # leaving only 100 USDT of margin headroom => qty = 100*5/100 = 5,
    # far below what risk/single-margin caps alone would allow.
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", stop_value=Decimal("94")),
        config=_config(),
        account=_account(total_initial_margin_used_usdt=Decimal("2900")),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.quantity == Decimal("5")


def test_evaluate_trade_intent_rejects_when_no_margin_headroom_left() -> None:
    evaluation = evaluate_trade_intent(
        _intent(side="LONG", stop_value=Decimal("94")),
        config=_config(),
        account=_account(total_initial_margin_used_usdt=Decimal("3000")),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert evaluation.reason_codes == [REASON_QUANTITY_NOT_POSITIVE]


# --- Multiple simultaneous failures collect all reason codes ---


def test_evaluate_trade_intent_collects_multiple_reason_codes() -> None:
    evaluation = evaluate_trade_intent(
        _intent(
            symbol="BTCUSDT",
            side="LONG",
            signal_source_date=NOW - timedelta(seconds=120),
            signal_received_at=NOW - timedelta(seconds=100),
        ),
        config=_config(max_concurrent_positions=1),
        account=_account(open_position_sides={"BTCUSDT": "SHORT"}, open_position_count=1),
        market_price=Decimal("100"),
        now=NOW,
    )

    assert evaluation.verdict == "REJECTED"
    assert set(evaluation.reason_codes) == {
        REASON_SOURCE_TOO_OLD,
        REASON_RECEIVE_LAG_TOO_HIGH,
        REASON_SYMBOL_CONFLICT,
        REASON_MAX_POSITIONS_REACHED,
    }
