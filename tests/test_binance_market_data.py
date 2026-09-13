from __future__ import annotations

from datetime import UTC, datetime

from telegram_trader.binance_market_data import (
    compute_snapshot_id,
    parse_active_perpetual_symbols,
)


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
