"""Multiple take-profit levels through the Execution Gateway, against a migrated PostgreSQL.

A long signal with several targets is approved, risk-evaluated and executed with a fake Binance
client: the filled quantity must be split equally across the targets, one reduce-only
`TAKE_PROFIT_MARKET` per level, each recorded and notified on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

# Reuse the seeding helpers and the `engine` fixture of the draft-edit integration tests.
from test_draft_edit_integration import (
    FakeMarket,
    _approve,
    _seed_signal,
    _settings,
)
from test_draft_edit_integration import (
    engine as _engine_fixture,
)

from telegram_trader.binance_trading_client import BinanceApiError
from telegram_trader.db import create_session_factory
from telegram_trader.execution_gateway import _execute_one, load_eligible_intents
from telegram_trader.models import ExchangeOrder, OutboxEvent, TakeProfitOrder
from telegram_trader.trade_intent_pipeline import run_risk_evaluation

pytestmark = pytest.mark.integration

engine = _engine_fixture  # re-exported so pytest finds the shared fixture here

NOW = datetime(2026, 9, 6, 8, 0, 30, tzinfo=UTC)


class _ExecutionMarket:
    def get_exchange_info(self) -> dict[str, Any]:
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    ],
                }
            ]
        }


class _FillingTradingClient:
    """The entry fills at once for `quantity`; algo placements succeed unless told to fail."""

    def __init__(self, quantity: str, *, fail_trigger_price: str | None = None) -> None:
        self._quantity = quantity
        self._fail_trigger_price = fail_trigger_price
        self.algo_calls: list[dict[str, Any]] = []
        self._placed = False

    def get_position_risk(self) -> list[dict[str, Any]]:
        return []

    def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        return {}

    def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        return {}

    def get_order(self, symbol: str, *, orig_client_order_id: str) -> dict[str, Any]:
        if not self._placed:
            raise BinanceApiError(400, -2013, "Order does not exist.")
        return {
            "status": "FILLED",
            "orderId": 11,
            "executedQty": self._quantity,
            "avgPrice": "100",
        }

    def place_order(self, **params: Any) -> dict[str, Any]:
        self._placed = True
        return {
            "status": "FILLED",
            "orderId": 11,
            "executedQty": self._quantity,
            "avgPrice": "100",
        }

    def place_algo_order(self, **params: Any) -> dict[str, Any]:
        self.algo_calls.append(params)
        if (
            self._fail_trigger_price is not None
            and params["type"] == "TAKE_PROFIT_MARKET"
            and Decimal(str(params["triggerPrice"])) == Decimal(self._fail_trigger_price)
        ):
            raise BinanceApiError(400, -4131, "rejected")
        return {"algoStatus": "NEW", "algoId": len(self.algo_calls)}

    def get_algo_order(self, *, client_algo_id: str) -> dict[str, Any]:
        raise BinanceApiError(400, -2013, "Order does not exist.")


def _execute(factory: Any, trading_client: _FillingTradingClient) -> Any:
    with factory() as session:
        ((trade_intent, risk_decision),) = load_eligible_intents(session)
    return _execute_one(
        factory,
        trade_intent,
        risk_decision,
        settings=_settings(),
        market_data_client=_ExecutionMarket(),
        trading_client=trading_client,
        clock=lambda: NOW,
        poll_timeout_seconds=0,
        poll_interval_seconds=0,
        protection_deadline_seconds=5,
    )


def _prepare(factory: Any, tmp_path: Path, targets: list[str]) -> None:
    row_id, _ = _seed_signal(factory, tmp_path, take_profits=targets)
    _approve(factory, row_id)
    run_risk_evaluation(
        factory, settings=_settings(), market_data_client=FakeMarket("100"), clock=lambda: NOW
    )


def test_quantity_is_split_equally_across_the_targets(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    _prepare(factory, tmp_path, ["110", "120", "130"])
    client = _FillingTradingClient("3.000")

    result = _execute(factory, client)

    assert result.final_state == "PROTECTED"
    take_profit_calls = [c for c in client.algo_calls if c["type"] == "TAKE_PROFIT_MARKET"]
    assert [Decimal(str(c["triggerPrice"])) for c in take_profit_calls] == [
        Decimal("110.0"),
        Decimal("120.0"),
        Decimal("130.0"),
    ]
    assert [Decimal(str(c["quantity"])) for c in take_profit_calls] == [Decimal("1.000")] * 3
    assert all(c["reduceOnly"] == "true" for c in take_profit_calls)
    assert len({c["clientAlgoId"] for c in take_profit_calls}) == 3
    stop_call = next(c for c in client.algo_calls if c["type"] == "STOP_MARKET")
    assert Decimal(str(stop_call["quantity"])) == Decimal("3.000")  # the stop covers everything

    with factory() as session:
        rows = list(session.scalars(select(TakeProfitOrder).order_by(TakeProfitOrder.level_index)))
        assert [(r.level_index, r.state) for r in rows] == [
            (0, "CONFIRMED"),
            (1, "CONFIRMED"),
            (2, "CONFIRMED"),
        ]
        assert sum(r.target_quantity for r in rows) == Decimal("3")
        orders = list(
            session.scalars(select(ExchangeOrder).where(ExchangeOrder.purpose == "TAKE_PROFIT"))
        )
        assert len(orders) == 3
        confirmed = list(
            session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "execution_take_profit_confirmed"
                )
            )
        )
        assert sorted(e.payload["level"] for e in confirmed) == ["1/3", "2/3", "3/3"]


def test_a_single_target_behaves_as_before(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    _prepare(factory, tmp_path, ["120"])
    client = _FillingTradingClient("3.000")

    _execute(factory, client)

    [call] = [c for c in client.algo_calls if c["type"] == "TAKE_PROFIT_MARKET"]
    assert Decimal(str(call["quantity"])) == Decimal("3.000")
    with factory() as session:
        [row] = session.scalars(select(TakeProfitOrder)).all()
        assert (row.level_index, row.state) == (0, "CONFIRMED")


def test_a_failed_level_does_not_block_the_other_levels(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    _prepare(factory, tmp_path, ["110", "120", "130"])
    client = _FillingTradingClient("3.000", fail_trigger_price="120.0")

    result = _execute(factory, client)

    assert result.final_state == "PROTECTED"
    assert "take-profit 2/3 FAILED" in result.detail
    with factory() as session:
        rows = list(session.scalars(select(TakeProfitOrder).order_by(TakeProfitOrder.level_index)))
        assert [(r.level_index, r.state) for r in rows] == [
            (0, "CONFIRMED"),
            (1, "FAILED"),
            (2, "CONFIRMED"),
        ]
        failed = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "execution_take_profit_failed")
            )
        )
        assert [e.payload["level"] for e in failed] == ["2/3"]
