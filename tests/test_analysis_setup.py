from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from telegram_trader.analysis_setup import (
    Candle,
    PriceZone,
    SetupState,
    SetupValidationError,
    evaluate_candles,
    parse_setup_response,
)

SOURCE = (
    "DOT 空\nEN：1.18-1.2\nSL：1.23上方\nTP：1.08-1.1、1.03-1.05\n"  # noqa: RUF001
    "小級別則是跌破1.18會破壞反彈的上升結構\n（個人擺在1.25）"  # noqa: RUF001
)
SYMBOLS = frozenset({"DOTUSDT"})


def _setup(**overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
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
        "evidence_quotes": ["EN：1.18-1.2"],  # noqa: RUF001
    }
    values.update(overrides)
    return values


def _response(*setups: dict[str, Any]) -> dict[str, Any]:
    return {"summary_zh": "DOT 偏空", "setups": list(setups)}


def test_parse_accepts_a_valid_setup() -> None:
    parsed = parse_setup_response(_response(_setup()), valid_symbols=SYMBOLS, source_text=SOURCE)

    assert parsed.rejected == []
    [setup] = parsed.setups
    assert setup.symbol == "DOTUSDT"
    assert setup.entry_zone == PriceZone(Decimal("1.18"), Decimal("1.2"))
    assert setup.stop_value == Decimal("1.25")
    assert setup.take_profits[0] == PriceZone(Decimal("1.08"), Decimal("1.1"))
    assert setup.confirm_rule == "REJECT"


def test_break_rule_only_when_trigger_agrees_with_direction() -> None:
    agreeing = _setup(trigger_type="BREAK_BELOW", trigger_price=1.18)
    contradicting = _setup(trigger_type="BREAK_ABOVE", trigger_price=1.18)

    [kept] = parse_setup_response(
        _response(agreeing), valid_symbols=SYMBOLS, source_text=SOURCE
    ).setups
    [fallback] = parse_setup_response(
        _response(contradicting), valid_symbols=SYMBOLS, source_text=SOURCE
    ).setups

    assert (kept.confirm_rule, kept.trigger_price) == ("BREAK", Decimal("1.18"))
    assert (fallback.confirm_rule, fallback.trigger_price) == ("REJECT", None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": "ETHUSDT"},
        {"evidence_quotes": ["this text is not in the message"]},
        {"stop_loss": 1.15},
        {"take_profits": [{"low": 1.2, "high": 1.3}]},
        {"entry_zone_low": -1},
    ],
)
def test_parse_rejects_only_the_bad_setup(overrides: dict[str, Any]) -> None:
    parsed = parse_setup_response(
        _response(_setup(**overrides), _setup()), valid_symbols=SYMBOLS, source_text=SOURCE
    )

    assert len(parsed.rejected) == 1
    assert len(parsed.setups) == 1


def test_parse_skips_non_actionable_setup_without_error() -> None:
    parsed = parse_setup_response(
        _response(_setup(side="NONE", entry_kind="NONE")),
        valid_symbols=SYMBOLS,
        source_text=SOURCE,
    )

    assert parsed.setups == [] and parsed.rejected == []


@pytest.mark.parametrize(
    "raw", [[], {"summary_zh": "x"}, {"summary_zh": "x", "setups": [], "extra": 1}]
)
def test_parse_rejects_malformed_top_level(raw: Any) -> None:
    with pytest.raises(SetupValidationError):
        parse_setup_response(raw, valid_symbols=SYMBOLS, source_text=SOURCE)


# ------------------------------------------------------------------ market rules

T0 = datetime(2026, 10, 3, 4, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=24)


def _candle(index: int, o: str, h: str, low: str, c: str) -> Candle:
    start = T0 + timedelta(minutes=15 * index)
    return Candle(
        open_time=start,
        close_time=start + timedelta(minutes=15),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def _state(**overrides: Any) -> SetupState:
    values: dict[str, Any] = {
        "side": "SHORT",
        "status": "WATCHING",
        "entry_zone": PriceZone(Decimal("1.18"), Decimal("1.20")),
        "confirm_rule": "REJECT",
        "trigger_price": None,
        "stop_value": Decimal("1.25"),
        "first_target": PriceZone(Decimal("1.08"), Decimal("1.10")),
        "cancel_if_target_first": True,
        "valid_from": T0,
        "expires_at": T0 + timedelta(hours=48),
    }
    values.update(overrides)
    return SetupState(**values)


def test_price_below_zone_does_nothing() -> None:
    result = evaluate_candles(_state(), [_candle(0, "1.15", "1.16", "1.14", "1.15")], now=NOW)

    assert result.status == "WATCHING" and result.events == ()


def test_touch_then_bearish_rejection_triggers_with_clamped_entry() -> None:
    candles = [
        _candle(0, "1.17", "1.19", "1.17", "1.185"),  # touches, bullish -> touch only
        _candle(1, "1.19", "1.195", "1.175", "1.1811"),  # bearish, closes inside zone
    ]

    result = evaluate_candles(_state(), candles, now=NOW)

    assert result.events == ("ZONE_TOUCHED", "TRIGGERED")
    assert result.status == "TRIGGERED"
    assert result.entry_price == Decimal("1.1811")
    assert result.touched_at == candles[0].close_time


def test_same_candle_can_touch_and_confirm() -> None:
    result = evaluate_candles(_state(), [_candle(0, "1.19", "1.195", "1.17", "1.18")], now=NOW)

    assert result.events == ("ZONE_TOUCHED", "TRIGGERED")


def test_close_above_zone_is_not_a_rejection() -> None:
    result = evaluate_candles(_state(), [_candle(0, "1.21", "1.22", "1.19", "1.205")], now=NOW)

    assert result.status == "ZONE_TOUCHED" and result.events == ("ZONE_TOUCHED",)


def test_break_rule_needs_close_beyond_trigger_and_clamps_entry() -> None:
    state = _state(confirm_rule="BREAK", trigger_price=Decimal("1.18"))
    candles = [
        _candle(0, "1.18", "1.19", "1.178", "1.185"),  # touch, close above trigger
        _candle(1, "1.185", "1.186", "1.17", "1.1798"),  # closes below 1.18
    ]

    result = evaluate_candles(state, candles, now=NOW)

    assert result.status == "TRIGGERED"
    assert result.entry_price == Decimal("1.18")  # clamped up into the zone


def test_stop_reached_before_entry_invalidates() -> None:
    result = evaluate_candles(_state(), [_candle(0, "1.2", "1.26", "1.19", "1.25")], now=NOW)

    assert result.status == "INVALIDATED" and result.reason == "STOP_REACHED"


def test_target_reached_first_cancels_only_when_requested() -> None:
    candles = [_candle(0, "1.14", "1.15", "1.09", "1.10")]

    cancelled = evaluate_candles(_state(), candles, now=NOW)
    ignored = evaluate_candles(_state(cancel_if_target_first=False), candles, now=NOW)

    assert cancelled.reason == "TARGET_REACHED_FIRST"
    assert ignored.status == "WATCHING"


def test_candles_before_valid_from_and_unclosed_candles_are_ignored() -> None:
    before = _candle(-1, "1.19", "1.195", "1.17", "1.18")
    unfinished = _candle(96, "1.19", "1.195", "1.17", "1.18")  # closes after NOW

    result = evaluate_candles(_state(), [before, unfinished], now=NOW)

    assert result.status == "WATCHING" and result.events == ()


def test_expiry() -> None:
    result = evaluate_candles(_state(), [], now=T0 + timedelta(hours=49))

    assert result.status == "EXPIRED"


def test_long_mirror_rejection() -> None:
    state = _state(
        side="LONG",
        entry_zone=PriceZone(Decimal("100"), Decimal("102")),
        stop_value=Decimal("97"),
        first_target=PriceZone(Decimal("110"), Decimal("112")),
    )
    candles = [_candle(0, "100.5", "104", "99.5", "101.5")]  # dips in, bullish close in zone

    result = evaluate_candles(state, candles, now=NOW)

    assert result.status == "TRIGGERED"
    assert result.entry_price == Decimal("101.5")
