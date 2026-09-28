from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from telegram_trader.risk_engine import (
    REASON_DAILY_LOSS_KILL_SWITCH,
    REASON_INVALID_STOP,
    REASON_MAX_POSITIONS_REACHED,
    REASON_PRICE_DEVIATION_TOO_HIGH,
    REASON_QUANTITY_NOT_POSITIVE,
    REASON_RECEIVE_LAG_TOO_HIGH,
    REASON_REFERENCE_PRICE_UNAVAILABLE,
    REASON_STALE_MARKET_DATA,
    REASON_SYMBOL_CONFLICT,
    AccountState,
    RiskConfig,
    RiskEvaluation,
    TradeIntentInput,
    compute_roe_stop_price,
    evaluate_trade_intent,
)

NOW = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


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
        "signal_source_date": NOW - timedelta(minutes=5),
        "signal_received_at": NOW - timedelta(minutes=5) + timedelta(seconds=1),
    }
    values.update(overrides)
    return TradeIntentInput(**values)  # type: ignore[arg-type]


def _evaluate(intent: TradeIntentInput, **overrides: object) -> RiskEvaluation:
    """Default `market_price`/`reference_price` agree (zero deviation) --

    a MARKET-entry test that cares about some *other* rule shouldn't
    incidentally trip BR-006's price-deviation check too.
    """
    kwargs: dict[str, object] = {
        "config": _config(),
        "account": _account(),
        "market_price": Decimal("100"),
        "reference_price": Decimal("100"),
        "now": NOW,
    }
    kwargs.update(overrides)
    return evaluate_trade_intent(intent, **kwargs)  # type: ignore[arg-type]


# --- compute_roe_stop_price (TBD-003 baseline formula) ---


def test_compute_roe_stop_price_long_is_below_entry() -> None:
    stop = compute_roe_stop_price(Decimal("100"), "LONG", leverage=5, roe_pct=Decimal("0.30"))
    assert stop == Decimal("94.0")


def test_compute_roe_stop_price_short_is_above_entry() -> None:
    stop = compute_roe_stop_price(Decimal("100"), "SHORT", leverage=5, roe_pct=Decimal("0.30"))
    assert stop == Decimal("106.0")


# --- golden path: approval ---


def test_evaluate_trade_intent_approves_clean_long_market_entry() -> None:
    evaluation = _evaluate(_intent(side="LONG", entry_type="MARKET"))

    assert evaluation.verdict == "APPROVED"
    assert evaluation.reason_codes == []
    assert evaluation.entry_price_used == Decimal("100")
    assert evaluation.computed_stop_price == Decimal("94.0")  # DEFAULT_ROE_30 baseline
    assert evaluation.quantity is not None
    assert evaluation.quantity > 0


def test_evaluate_trade_intent_uses_author_stop_when_present() -> None:
    evaluation = _evaluate(_intent(side="LONG", entry_type="MARKET", stop_value=Decimal("90")))

    assert evaluation.verdict == "APPROVED"
    assert evaluation.computed_stop_price == Decimal("90")


def test_evaluate_trade_intent_limit_entry_uses_signal_price_not_market_price() -> None:
    evaluation = _evaluate(
        _intent(
            side="LONG",
            entry_type="LIMIT",
            entry_values=[Decimal("95")],
            stop_value=Decimal("90"),
        ),
        market_price=Decimal("200"),  # far from the limit price -- must not be used
        reference_price=None,  # LIMIT ignores reference_price entirely
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.entry_price_used == Decimal("95")


def test_evaluate_trade_intent_range_entry_uses_midpoint() -> None:
    evaluation = _evaluate(
        _intent(
            side="LONG",
            entry_type="RANGE",
            entry_values=[Decimal("90"), Decimal("100")],
            stop_value=Decimal("80"),
        ),
        market_price=Decimal("200"),
        reference_price=None,
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.entry_price_used == Decimal("95")


# --- BR-012: fail closed on missing market data ---


def test_evaluate_trade_intent_rejects_when_market_price_missing() -> None:
    evaluation = _evaluate(_intent(), market_price=None)

    assert evaluation.verdict == "REJECTED"
    assert evaluation.reason_codes == [REASON_STALE_MARKET_DATA]
    assert evaluation.quantity is None


# --- BR-011: daily loss kill switch ---


def test_evaluate_trade_intent_flags_daily_kill_switch_as_advisory_not_rejected() -> None:
    """Superseded 2026-09-25 (P6-NOTICE-007): every trade is user-reviewed

    before placement, so this no longer blocks -- it's a visible flag.
    """
    evaluation = _evaluate(_intent(), account=_account(daily_realized_loss_pct=Decimal("-0.06")))

    assert evaluation.verdict == "APPROVED"
    assert evaluation.reason_codes == []
    assert REASON_DAILY_LOSS_KILL_SWITCH in evaluation.advisory_codes


def test_evaluate_trade_intent_allows_loss_just_under_kill_switch() -> None:
    evaluation = _evaluate(_intent(), account=_account(daily_realized_loss_pct=Decimal("-0.0599")))

    assert evaluation.verdict == "APPROVED"


# --- BR-006, revised: price deviation replaces raw elapsed time for MARKET entries ---


def test_evaluate_trade_intent_approves_market_entry_long_after_approval_if_price_unmoved() -> None:
    """The whole point of the P6-LIMIT-004 fix: a signal approved several

    minutes late is fine if the price hasn't moved -- unlike the old
    raw-elapsed-time check, this must NOT reject on age alone.
    """
    evaluation = _evaluate(
        _intent(
            signal_source_date=NOW - timedelta(hours=2),
            signal_received_at=NOW - timedelta(hours=2) + timedelta(seconds=1),
        ),
        market_price=Decimal("100"),
        reference_price=Decimal("100"),
    )

    assert evaluation.verdict == "APPROVED"


def test_evaluate_trade_intent_rejects_when_price_deviation_too_high() -> None:
    # 50bps of 100 = 0.50; a 1.00 move is 100bps, well over the limit.
    evaluation = _evaluate(_intent(), market_price=Decimal("101"), reference_price=Decimal("100"))

    assert evaluation.verdict == "REJECTED"
    assert REASON_PRICE_DEVIATION_TOO_HIGH in evaluation.reason_codes


def test_evaluate_trade_intent_allows_price_deviation_just_under_limit() -> None:
    # 49bps of 100 = 0.49 -- under the 50bps limit.
    evaluation = _evaluate(
        _intent(), market_price=Decimal("100.49"), reference_price=Decimal("100")
    )

    assert evaluation.verdict == "APPROVED"


def test_evaluate_trade_intent_rejects_market_entry_when_reference_price_unavailable() -> None:
    evaluation = _evaluate(_intent(entry_type="MARKET"), reference_price=None)

    assert evaluation.verdict == "REJECTED"
    assert REASON_REFERENCE_PRICE_UNAVAILABLE in evaluation.reason_codes


def test_evaluate_trade_intent_rejects_high_receive_lag() -> None:
    evaluation = _evaluate(
        _intent(
            signal_source_date=NOW - timedelta(seconds=20),
            signal_received_at=NOW - timedelta(seconds=8),
        )
    )

    assert evaluation.verdict == "REJECTED"
    assert REASON_RECEIVE_LAG_TOO_HIGH in evaluation.reason_codes


# --- BR-007: symbol conflict (advisory since 2026-09-25, P6-NOTICE-007) ---


def test_evaluate_trade_intent_flags_opposite_side_conflict_as_advisory_not_rejected() -> None:
    evaluation = _evaluate(
        _intent(symbol="BTCUSDT", side="LONG"),
        account=_account(open_position_sides={"BTCUSDT": "SHORT"}, open_position_count=1),
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.reason_codes == []
    assert REASON_SYMBOL_CONFLICT in evaluation.advisory_codes


def test_evaluate_trade_intent_allows_same_side_same_symbol() -> None:
    evaluation = _evaluate(
        _intent(symbol="BTCUSDT", side="LONG"),
        account=_account(open_position_sides={"BTCUSDT": "LONG"}, open_position_count=1),
    )

    assert evaluation.verdict == "APPROVED"


# --- BR-010: max concurrent positions (advisory since 2026-09-25, P6-NOTICE-007) ---


def test_evaluate_trade_intent_flags_max_positions_reached_as_advisory_not_rejected() -> None:
    evaluation = _evaluate(
        _intent(symbol="ETHUSDT"),
        config=_config(max_concurrent_positions=3),
        account=_account(open_position_count=3),
    )

    assert evaluation.verdict == "APPROVED"
    assert evaluation.reason_codes == []
    assert REASON_MAX_POSITIONS_REACHED in evaluation.advisory_codes


# --- Stop-side validation ---


def test_evaluate_trade_intent_rejects_stop_on_wrong_side_for_long() -> None:
    evaluation = _evaluate(
        _intent(side="LONG", stop_value=Decimal("110"))
    )  # above entry, invalid for LONG

    assert evaluation.verdict == "REJECTED"
    assert REASON_INVALID_STOP in evaluation.reason_codes


def test_evaluate_trade_intent_rejects_stop_on_wrong_side_for_short() -> None:
    evaluation = _evaluate(
        _intent(side="SHORT", stop_value=Decimal("90"))
    )  # below entry, invalid for SHORT

    assert evaluation.verdict == "REJECTED"
    assert REASON_INVALID_STOP in evaluation.reason_codes


# --- BR-009: flat position-size sizing (superseded 2026-09-25, P6-NOTICE-007) ---


def test_evaluate_trade_intent_sizes_by_flat_position_size_pct() -> None:
    # equity=10000, position_size_pct=20% => margin=2000; leverage=5x =>
    # notional=10000; entry_price=100 (market) => quantity=100.
    evaluation = _evaluate(_intent(side="LONG", stop_value=Decimal("90")))

    assert evaluation.verdict == "APPROVED"
    assert evaluation.quantity == Decimal("100")


def test_evaluate_trade_intent_sizing_is_independent_of_stop_distance() -> None:
    """The whole point of the 2026-09-25 change: sizing no longer derives

    from stop distance at all -- the user sets stops by chart structure,
    not by a risk-based distance, so a near stop and a far stop must size
    identically.
    """
    near_stop = _evaluate(_intent(side="LONG", stop_value=Decimal("99")))
    far_stop = _evaluate(_intent(side="LONG", stop_value=Decimal("50")))

    assert near_stop.quantity == far_stop.quantity == Decimal("100")


def test_evaluate_trade_intent_sizing_scales_with_equity() -> None:
    evaluation = _evaluate(
        _intent(side="LONG", stop_value=Decimal("90")),
        account=_account(equity_usdt=Decimal("5000")),
    )

    # margin=1000, notional=5000, entry_price=100 => quantity=50.
    assert evaluation.quantity == Decimal("50")


def test_evaluate_trade_intent_rejects_when_equity_is_zero() -> None:
    evaluation = _evaluate(
        _intent(side="LONG", stop_value=Decimal("90")),
        account=_account(equity_usdt=Decimal("0")),
    )

    assert evaluation.verdict == "REJECTED"
    assert evaluation.reason_codes == [REASON_QUANTITY_NOT_POSITIVE]


# --- Multiple simultaneous failures collect all reason codes ---


def test_evaluate_trade_intent_collects_multiple_reason_codes() -> None:
    evaluation = _evaluate(
        _intent(
            symbol="BTCUSDT",
            side="LONG",
            signal_source_date=NOW - timedelta(seconds=20),
            signal_received_at=NOW - timedelta(seconds=8),
        ),
        config=_config(max_concurrent_positions=1),
        account=_account(open_position_sides={"BTCUSDT": "SHORT"}, open_position_count=1),
        market_price=Decimal("101"),
        reference_price=Decimal("100"),
    )

    assert evaluation.verdict == "REJECTED"
    assert set(evaluation.reason_codes) == {
        REASON_PRICE_DEVIATION_TOO_HIGH,
        REASON_RECEIVE_LAG_TOO_HIGH,
    }
    assert set(evaluation.advisory_codes) == {
        REASON_SYMBOL_CONFLICT,
        REASON_MAX_POSITIONS_REACHED,
    }
