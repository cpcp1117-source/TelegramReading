"""Position monitor (Phase 6, 2026-09-29 explicit user request): tells the user when a
position the Execution Gateway opened has been closed -- by its stop-loss, by its
take-profit, or by anything else (a manual close, liquidation) -- and cancels the
other, now-orphaned reduce-only order so it cannot close a later position on the
same symbol (user-confirmed: cancel automatically and say so).

REST polling, not the WebSocket User Data Stream (CS-BN-003): one `positionRisk`
call per open symbol per cycle is cheap at this bot's volume, has no listenKey
lifecycle to keep alive, and a few seconds of notification latency is fine for a
semi-automatic bot where the user acts on the message by hand.

"Closed" means the symbol's position is flat. The cause is read from the algo
orders themselves: the one with a non-empty `actualOrderId` is the one that
triggered. Exit price and realized PnL come from this account's own fills since
the entry filled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.execution_gateway import _notify, _write_lifecycle, close_side_for
from telegram_trader.models import ExchangeOrder, OrderLifecycle, RealizedPnl, TradeIntent

LOGGER = logging.getLogger(__name__)

_OPEN_POSITION_STATES = ("PROTECTED", "PROTECTION_FAILED")
_ALGO_STILL_OPEN_STATUSES = ("NEW",)
# Mirrors ck_exchange_order_status_valid (migration 0020).
_RECORDABLE_STATUSES = frozenset(
    {
        "NEW",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "PENDING_CANCEL",
        "REJECTED",
        "EXPIRED",
        "EXPIRED_IN_MATCH",
        "TRIGGERED",
        "TRIGGERING",
        "FINISHED",
        "UNKNOWN",
    }
)
_CLOSE_REASON_LABELS = {
    "STOP_LOSS": "停損觸發",
    "TAKE_PROFIT": "停利觸發",
    "OTHER": "非停損/停利（手動平倉、強平或其他）",
}


class PositionMonitorError(RuntimeError):
    """At least one open position could not be checked this cycle."""


@dataclass(frozen=True, slots=True)
class OpenPosition:
    intent_id: str
    symbol: str
    side: str
    decision_id: str
    entry_client_order_id: str
    correlation_id: str
    latest_revision: int
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class MonitorResult:
    intent_id: str
    symbol: str
    close_reason: str


def load_open_positions(session: Session) -> list[OpenPosition]:
    """Intents whose latest lifecycle row says a position is open (entry filled,
    protection placed or failed) and that have not been marked CLOSED yet."""
    latest = (
        select(OrderLifecycle.intent_id, func.max(OrderLifecycle.revision).label("revision"))
        .group_by(OrderLifecycle.intent_id)
        .subquery()
    )
    rows = session.execute(
        select(OrderLifecycle)
        .join(
            latest,
            (OrderLifecycle.intent_id == latest.c.intent_id)
            & (OrderLifecycle.revision == latest.c.revision),
        )
        .where(OrderLifecycle.state.in_(_OPEN_POSITION_STATES))
        .order_by(OrderLifecycle.transitioned_at)
    ).scalars()
    positions: list[OpenPosition] = []
    for lifecycle in rows:
        filled_at = session.scalar(
            select(OrderLifecycle.transitioned_at).where(
                OrderLifecycle.intent_id == lifecycle.intent_id,
                OrderLifecycle.state == "FILLED",
            )
        )
        positions.append(
            OpenPosition(
                intent_id=lifecycle.intent_id,
                symbol=lifecycle.symbol,
                side=lifecycle.side,
                decision_id=lifecycle.decision_id,
                entry_client_order_id=lifecycle.entry_client_order_id,
                correlation_id=lifecycle.correlation_id,
                latest_revision=lifecycle.revision,
                filled_at=filled_at or lifecycle.transitioned_at,
            )
        )
    return positions


def _is_flat(trading_client: Any, symbol: str) -> bool:
    rows = trading_client.get_position_risk(symbol)
    return all(Decimal(str(row.get("positionAmt", "0"))) == 0 for row in rows)


def _triggered(algo_order: dict[str, Any]) -> bool:
    return str(algo_order.get("actualOrderId") or "") not in ("", "0")


def _summarize_closing_fills(
    trades: list[dict[str, Any]], *, close_side: str
) -> dict[str, str | None]:
    closing = [trade for trade in trades if trade.get("side") == close_side]
    quantity = sum((Decimal(str(t["qty"])) for t in closing), Decimal("0"))
    if quantity == 0:
        return {"exit_price": None, "closed_quantity": None, "realized_pnl": None}
    notional = sum((Decimal(str(t["qty"])) * Decimal(str(t["price"])) for t in closing), Decimal())
    pnl = sum((Decimal(str(t.get("realizedPnl", "0"))) for t in closing), Decimal("0"))
    commission = sum((Decimal(str(t.get("commission", "0"))) for t in closing), Decimal("0"))
    return {
        "exit_price": f"{(notional / quantity).normalize():f}",
        "closed_quantity": f"{quantity.normalize():f}",
        "realized_pnl": f"{pnl.normalize():f}",
        "exit_commission": f"{commission.normalize():f}",
    }


def _decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value else None


def _record_realized_pnl(
    session: Session,
    position: OpenPosition,
    close_reason: str,
    fills: dict[str, str | None],
    now: datetime,
) -> None:
    """The close as a queryable row (migration 0023), same transaction as the CLOSED state."""
    channel_id = session.scalar(
        select(TradeIntent.channel_id).where(TradeIntent.intent_id == position.intent_id)
    )
    entry_price = session.scalar(
        select(ExchangeOrder.avg_fill_price).where(
            ExchangeOrder.intent_id == position.intent_id, ExchangeOrder.purpose == "ENTRY"
        )
    )
    session.add(
        RealizedPnl(
            intent_id=position.intent_id,
            channel_id=channel_id,
            symbol=position.symbol,
            side=position.side,
            close_reason=close_reason,
            quantity=_decimal_or_none(fills.get("closed_quantity")),
            entry_price=entry_price,
            exit_price=_decimal_or_none(fills.get("exit_price")),
            realized_pnl=_decimal_or_none(fills.get("realized_pnl")),
            exit_commission=_decimal_or_none(fills.get("exit_commission")),
            opened_at=position.filled_at,
            closed_at=now,
        )
    )


def _check_one(
    session_factory: sessionmaker[Session],
    position: OpenPosition,
    *,
    trading_client: Any,
    clock: Callable[[], datetime],
) -> MonitorResult | None:
    if not _is_flat(trading_client, position.symbol):
        return None

    with session_factory() as session:
        algo_rows = session.scalars(
            select(ExchangeOrder).where(
                ExchangeOrder.intent_id == position.intent_id,
                ExchangeOrder.purpose.in_(("PROTECTION", "TAKE_PROFIT")),
            )
        ).all()
        algo_orders = [(row.purpose, row.client_order_id) for row in algo_rows]

    close_reason = "OTHER"
    cancelled: list[str] = []
    cancel_failures: list[dict[str, str]] = []
    statuses: dict[str, str] = {}
    for purpose, client_order_id in algo_orders:
        try:
            algo_order = trading_client.get_algo_order(client_algo_id=client_order_id)
        except Exception as error:
            LOGGER.exception(
                "could not query algo order after position closed",
                extra={"context": {"client_order_id": client_order_id}},
            )
            cancel_failures.append(
                {"purpose": purpose, "client_order_id": client_order_id, "reason": repr(error)}
            )
            continue
        status = str(algo_order.get("algoStatus", "UNKNOWN"))
        # A status outside exchange_order's CHECK vocabulary must not crash the
        # close notification; it is still visible in the logs.
        statuses[client_order_id] = status if status in _RECORDABLE_STATUSES else "UNKNOWN"
        if _triggered(algo_order):
            close_reason = "STOP_LOSS" if purpose == "PROTECTION" else "TAKE_PROFIT"
        elif status in _ALGO_STILL_OPEN_STATUSES:
            try:
                trading_client.cancel_algo_order(client_algo_id=client_order_id)
            except Exception as error:
                LOGGER.exception(
                    "could not cancel leftover algo order",
                    extra={"context": {"client_order_id": client_order_id}},
                )
                cancel_failures.append(
                    {
                        "purpose": purpose,
                        "client_order_id": client_order_id,
                        "reason": repr(error),
                    }
                )
            else:
                cancelled.append(purpose)
                statuses[client_order_id] = "CANCELED"

    close_side = close_side_for(position.side)
    try:
        trades = trading_client.get_user_trades(
            position.symbol, start_time_ms=int(position.filled_at.timestamp() * 1000) - 1000
        )
        fills = _summarize_closing_fills(trades, close_side=close_side)
    except Exception as error:
        LOGGER.exception(
            "could not load closing fills", extra={"context": {"symbol": position.symbol}}
        )
        fills = {"fills_unavailable": repr(error)}

    now = clock()
    with session_factory.begin() as session:
        claimed = _write_lifecycle(
            session,
            intent_id=position.intent_id,
            decision_id=position.decision_id,
            revision=position.latest_revision + 1,
            symbol=position.symbol,
            side=position.side,
            entry_client_order_id=position.entry_client_order_id,
            state="CLOSED",
            correlation_id=position.correlation_id,
            transition_reason=f"position_closed_{close_reason.lower()}",
            now=now,
        )
        if not claimed:
            return None
        for client_order_id, status in statuses.items():
            session.execute(
                update(ExchangeOrder)
                .where(ExchangeOrder.client_order_id == client_order_id)
                .values(status=status, last_checked_at=now)
            )
        _record_realized_pnl(session, position, close_reason, fills, now)
        _notify(
            session,
            intent_id=position.intent_id,
            event_type="execution_position_closed",
            payload={
                "symbol": position.symbol,
                "side": position.side,
                "close_reason": _CLOSE_REASON_LABELS[close_reason],
                **fills,
                "cancelled_leftover": ", ".join(
                    "停損單" if purpose == "PROTECTION" else "停利單" for purpose in cancelled
                )
                or None,
            },
            now=now,
        )
        if cancel_failures:
            _notify(
                session,
                intent_id=position.intent_id,
                event_type="execution_leftover_cancel_failed",
                payload={
                    "symbol": position.symbol,
                    "orders": "; ".join(
                        f"{'停損單' if f['purpose'] == 'PROTECTION' else '停利單'} "
                        f"{f['client_order_id']}: {f['reason']}"
                        for f in cancel_failures
                    ),
                },
                now=now,
            )
    return MonitorResult(position.intent_id, position.symbol, close_reason)


def run_position_monitor(
    session_factory: sessionmaker[Session],
    *,
    trading_client: Any,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[MonitorResult]:
    with session_factory() as session:
        positions = load_open_positions(session)
    results: list[MonitorResult] = []
    errors: list[str] = []
    for position in positions:
        try:
            result = _check_one(
                session_factory, position, trading_client=trading_client, clock=clock
            )
        except Exception as error:
            # One bad symbol must not stop the others being checked. The failure is
            # re-raised after the loop so the worker notifies once, not every cycle.
            LOGGER.exception(
                "position monitor failed for intent",
                extra={"context": {"intent_id": position.intent_id}},
            )
            errors.append(f"{position.symbol}: {error!r}")
            continue
        if result is not None:
            results.append(result)
    if errors:
        raise PositionMonitorError("; ".join(errors))
    return results
