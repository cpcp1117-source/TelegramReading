from __future__ import annotations

from decimal import Decimal

import pytest

from telegram_trader.signal_parser import (
    determine_status,
    parse_fields,
    resolve_stop,
    single_resolved_symbol,
)

SYMBOL_STATUSES = (None, "INVALID", "PENDING_MARKET_DATA", "VALID")


# --- Real-fixture-grounded cases (monster-currency-universe.md §5) ---


def test_parse_fields_clear_long_with_size_modifier_ignores_modifier() -> None:
    """MONSTER-001/004: "小多" is LONG; "小" must never affect extracted fields."""
    parsed = parse_fields("CHIP 小多 現價進場")

    assert parsed.side == "LONG"
    assert parsed.is_signal_attempt is True


def test_parse_fields_risk_flagged_long_modifier_still_detected() -> None:
    """MONSTER-018: "風險短多" is LONG (risk-flagged), not SHORT."""
    parsed = parse_fields("ICP 風險短多 市價進場")

    assert parsed.side == "LONG"


def test_parse_fields_clear_short() -> None:
    """MONSTER-011: plain SHORT, market entry, "福利單" label is irrelevant text."""
    parsed = parse_fields("CHIP 空 福利單 市價進場")

    assert parsed.side == "SHORT"


def test_parse_fields_promotional_text_is_not_a_signal_attempt() -> None:
    """Roughly half of real traffic: symbol hashtag + brag text, no side/entry/SL/TP."""
    parsed = parse_fields("$BTC 衝高已經翻倍了,恭喜早期跟上車的朋友們")

    assert parsed.is_signal_attempt is False
    assert parsed.side is None


def test_parse_fields_explicit_wait_recommendation_is_not_a_signal_attempt() -> None:
    """MONSTER-010: explicit "建議觀望" must produce no attempt even with a symbol present."""
    parsed = parse_fields("BTC 近期偏多但目前建議觀望")

    assert parsed.is_signal_attempt is False


def test_parse_fields_market_entry_with_quoted_reference_price() -> None:
    """MONSTER-014: "entry=MARKET @0.1950" -- MARKET even with a reference price quoted."""
    parsed = parse_fields("ARB 空 市價進場附近0.1950")

    assert parsed.side == "SHORT"
    assert parsed.entry_type == "MARKET"
    assert parsed.entry_values == [Decimal("0.1950")]


def test_parse_fields_author_sl_and_tp_range_entry_stays_market() -> None:
    """MONSTER-015: TP=7.28-7.7, SL=6.83 (author), entry is still MARKET (no limit keyword)."""
    parsed = parse_fields("UNI 多 止盈7.28-7.7 止損6.83")

    assert parsed.side == "LONG"
    assert parsed.entry_type == "MARKET"
    assert parsed.entry_values == []
    assert parsed.stop_value == Decimal("6.83")
    assert parsed.take_profits == [Decimal("7.28"), Decimal("7.7")]
    assert parsed.is_signal_attempt is True


def test_parse_fields_leverage_and_price_without_side_text_is_incomplete_side() -> None:
    """MONSTER-017: leverage + entry price given, side only implied by emoji -- must be None."""
    parsed = parse_fields("ARB 50倍 進場0.1686 🚀")

    assert parsed.side is None
    assert parsed.entry_values == [Decimal("0.1686")]
    assert parsed.is_signal_attempt is True  # has an entry price, so still an "attempt"


def test_parse_fields_negated_direction_is_not_extracted_as_that_side() -> None:
    parsed = parse_fields("目前不要追多,等回踩再說")

    assert parsed.side is None


# --- Synthetic-only cases (no real fixture exercises these yet) ---


def test_parse_fields_cancel_keyword_detected_synthetic() -> None:
    parsed = parse_fields("取消上一個信號")

    assert parsed.cancel_detected is True
    assert parsed.is_signal_attempt is True


def test_parse_fields_close_keyword_detected_synthetic() -> None:
    parsed = parse_fields("平倉,先落袋為安")

    assert parsed.cancel_detected is True


def test_parse_fields_limit_entry_single_price_synthetic() -> None:
    parsed = parse_fields("BTC 多 限價0.50 進場")

    assert parsed.entry_type == "LIMIT"
    assert parsed.entry_values == [Decimal("0.50")]


def test_parse_fields_range_entry_two_prices_synthetic() -> None:
    parsed = parse_fields("BTC 多 限價0.50-0.55")

    assert parsed.entry_type == "RANGE"
    assert parsed.entry_values == [Decimal("0.50"), Decimal("0.55")]


def test_resolve_stop_no_stop_falls_back_to_default_roe_30() -> None:
    value, origin = resolve_stop(None, "LONG", Decimal("100"))

    assert value is None
    assert origin == "DEFAULT_ROE_30"


def test_resolve_stop_valid_author_stop_is_accepted() -> None:
    value, origin = resolve_stop(Decimal("90"), "LONG", Decimal("100"))

    assert value == Decimal("90")
    assert origin == "AUTHOR"


def test_resolve_stop_wrong_side_stop_against_known_entry_falls_back_synthetic() -> None:
    """LONG with a stop *above* the entry reference is self-contradictory (BR-004)."""
    value, origin = resolve_stop(Decimal("110"), "LONG", Decimal("100"))

    assert value is None
    assert origin == "DEFAULT_ROE_30"


def test_resolve_stop_accepted_without_validation_when_no_entry_reference() -> None:
    """The common bare-MARKET case: no reference price, so no side check is possible."""
    value, origin = resolve_stop(Decimal("999999"), "LONG", None)

    assert value == Decimal("999999")
    assert origin == "AUTHOR"


# --- single_resolved_symbol / determine_status ---


def test_single_resolved_symbol_requires_exactly_one_candidate() -> None:
    assert single_resolved_symbol([]) is None
    assert (
        single_resolved_symbol(
            [{"symbol": "BTCUSDT", "status": "VALID"}, {"symbol": "ETHUSDT", "status": "VALID"}]
        )
        is None
    )
    assert single_resolved_symbol([{"symbol": "BTCUSDT", "status": "VALID"}]) == (
        "BTCUSDT",
        "VALID",
    )


@pytest.mark.parametrize("symbol_status", SYMBOL_STATUSES)
def test_determine_status_without_side_is_always_incomplete(symbol_status: str | None) -> None:
    assert determine_status(symbol_status, None) == "INCOMPLETE"


def test_determine_status_missing_or_invalid_symbol_is_incomplete() -> None:
    assert determine_status(None, "LONG") == "INCOMPLETE"
    assert determine_status("INVALID", "LONG") == "INCOMPLETE"


def test_determine_status_pending_market_data_never_reaches_validated() -> None:
    """The scope decision this slice was built around: dynamic-scope symbols stay NEW."""
    assert determine_status("PENDING_MARKET_DATA", "LONG") == "NEW"
    assert determine_status("PENDING_MARKET_DATA", "SHORT") == "NEW"


def test_determine_status_valid_symbol_and_side_is_validated() -> None:
    assert determine_status("VALID", "LONG") == "VALIDATED"
