from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from telegram_trader.binance_market_data import (
    compute_snapshot_id,
    get_symbol_filters,
    parse_active_perpetual_symbols,
    round_price,
    round_quantity,
)

_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "tickSize": "0.10",
                    "minPrice": "0",
                    "maxPrice": "0",
                },
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0", "maxQty": "0"},
            ],
        }
    ]
}


def test_parse_active_perpetual_symbols_filters_status_and_contract_type() -> None:
    exchange_info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
            {"symbol": "ETHUSDT", "status": "BREAK", "contractType": "PERPETUAL"},
            {"symbol": "BTCUSD_240329", "status": "TRADING", "contractType": "CURRENT_QUARTER"},
            {"symbol": "SOLUSDC", "status": "TRADING", "contractType": "PERPETUAL"},
        ]
    }

    active = parse_active_perpetual_symbols(exchange_info)

    assert active == frozenset({"BTCUSDT", "SOLUSDC"})


def test_parse_active_perpetual_symbols_handles_empty_response() -> None:
    assert parse_active_perpetual_symbols({"symbols": []}) == frozenset()
    assert parse_active_perpetual_symbols({}) == frozenset()


def test_compute_snapshot_id_is_deterministic() -> None:
    fetched_at = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    first = compute_snapshot_id(fetched_at, frozenset({"BTCUSDT", "ETHUSDT"}))
    second = compute_snapshot_id(fetched_at, frozenset({"ETHUSDT", "BTCUSDT"}))

    assert first == second


def test_compute_snapshot_id_changes_with_symbols() -> None:
    fetched_at = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    a = compute_snapshot_id(fetched_at, frozenset({"BTCUSDT"}))
    b = compute_snapshot_id(fetched_at, frozenset({"ETHUSDT"}))

    assert a != b


def test_compute_snapshot_id_changes_with_fetched_at() -> None:
    symbols = frozenset({"BTCUSDT"})

    a = compute_snapshot_id(datetime(2026, 9, 13, 12, 0, tzinfo=UTC), symbols)
    b = compute_snapshot_id(datetime(2026, 9, 13, 13, 0, tzinfo=UTC), symbols)

    assert a != b


# --- get_symbol_filters / round_price / round_quantity (Phase 6 Slice 2a, FR-017) ---


def test_get_symbol_filters_extracts_tick_and_step_size() -> None:
    filters = get_symbol_filters(_EXCHANGE_INFO, "BTCUSDT")

    assert filters.tick_size == Decimal("0.10")
    assert filters.step_size == Decimal("0.001")


def test_get_symbol_filters_raises_when_symbol_missing() -> None:
    with pytest.raises(ValueError, match="ETHUSDT"):
        get_symbol_filters(_EXCHANGE_INFO, "ETHUSDT")


def test_get_symbol_filters_raises_when_filters_missing() -> None:
    with pytest.raises(ValueError, match="PRICE_FILTER"):
        get_symbol_filters({"symbols": [{"symbol": "XRPUSDT", "filters": []}]}, "XRPUSDT")


def test_round_price_truncates_to_tick_size() -> None:
    filters = get_symbol_filters(_EXCHANGE_INFO, "BTCUSDT")

    assert round_price(Decimal("83766.67"), filters) == Decimal("83766.60")


def test_round_quantity_truncates_to_step_size() -> None:
    filters = get_symbol_filters(_EXCHANGE_INFO, "BTCUSDT")

    assert round_quantity(Decimal("0.06939"), filters) == Decimal("0.069")


def test_round_never_rounds_up_past_a_filter_limit() -> None:
    """Truncation, not nearest-rounding -- rounding up could exceed a notional cap."""
    filters = get_symbol_filters(_EXCHANGE_INFO, "BTCUSDT")

    assert round_quantity(Decimal("0.0699999"), filters) == Decimal("0.069")
