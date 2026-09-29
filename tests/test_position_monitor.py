from __future__ import annotations

from telegram_trader.position_monitor import _summarize_closing_fills, _triggered


def test_triggered_uses_actual_order_id() -> None:
    assert _triggered({"algoStatus": "FINISHED", "actualOrderId": "3511034598"})
    assert not _triggered({"algoStatus": "NEW", "actualOrderId": ""})
    assert not _triggered({"algoStatus": "CANCELED", "actualOrderId": "0"})
    assert not _triggered({"algoStatus": "NEW"})


def test_summarize_closing_fills_weights_price_and_sums_pnl() -> None:
    trades = [
        {"side": "BUY", "qty": "100", "price": "1.50", "realizedPnl": "0", "commission": "0.1"},
        {"side": "SELL", "qty": "60", "price": "1.60", "realizedPnl": "6", "commission": "0.05"},
        {"side": "SELL", "qty": "40", "price": "1.55", "realizedPnl": "2", "commission": "0.03"},
    ]

    summary = _summarize_closing_fills(trades, close_side="SELL")

    assert summary == {
        "exit_price": "1.58",
        "closed_quantity": "100",
        "realized_pnl": "8",
        "exit_commission": "0.08",
    }


def test_summarize_closing_fills_without_closing_trades() -> None:
    summary = _summarize_closing_fills(
        [{"side": "BUY", "qty": "1", "price": "1"}], close_side="SELL"
    )

    assert summary["exit_price"] is None
    assert summary["realized_pnl"] is None
