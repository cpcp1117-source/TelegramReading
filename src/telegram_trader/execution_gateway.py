"""Execution Gateway (FR-017/018/019, Phase 6 Slice 2a).

Places real Binance USDⓈ-M Futures **Testnet** orders for `RISK_APPROVED`
trade intents: entry order -> confirmed real fill -> a `STOP_MARKET`
protection order and (if the signal has one) a `TAKE_PROFIT_MARKET` order,
both `reduceOnly`.

**No automatic Emergency Close** (changed 2026-09-29, explicit user
request): if the protection order cannot be confirmed within FR-019's
5-second deadline, this module notifies and stops -- it does not close the
position. The user reviews every trade already (Control Bot Approve) and
wants the same control over an unprotected position: they set the stop
themselves rather than have the system act on their behalf. `_emergency_close`
still exists (tested, kept for a possible future manual "close" command)
but nothing here calls it automatically anymore.

**Every outcome is notified** (added 2026-09-29): entry submit/fill,
protection confirm/fail, take-profit confirm/fail, and expiry/cancellation
all write an `outbox_events` row (via `_notify`) alongside the existing
`audit_events` row -- `control_bot.py` polls the outbox and forwards each
as a Telegram message. Execution Gateway itself has no Telegram credential
(credential-handoff.md's boundary is unchanged); the outbox is the
decoupling mechanism, not a new direct dependency.

Explicitly out of scope this slice (see docs/phase-6/known-issues.md for
the named, deliberate limitations, not silent gaps):
- Reconciliation-on-restart (FR-020) / WebSocket User Data Stream
  (CS-BN-003) -- this module polls REST and must run to completion,
  uninterrupted, one operator at a time.
- Dynamic protection/take-profit order resizing on additional partial
  fills (CS-BN-005) -- one `ProtectionOrder`/`TakeProfitOrder` per intent,
  sized to whatever was filled by the time polling stops.
- Multiple take-profit levels -- only `trade_intent.take_profits[0]` is
  ever used, for the full filled quantity.
- System Pause wiring (FR-021) -- `system_control_state` is not built.
- Production credentials/environment (FR-023) -- `environment` is always
  `'TESTNET'` (see config.py's `execution_environment`, hardcoded
  `Literal["TESTNET"]` -- there is no Production value to select yet).

BR-007 (symbol conflict) / BR-010 (max concurrent positions), rechecked
here against *real* Binance account/position data (replacing Slice 1's
clean-slate `AccountState` approximation), stay advisory-only -- logged as
an audit event, never blocking order placement. This mirrors
`risk_engine.RiskEvaluation.advisory_codes`'s own policy, confirmed with
the user for the same reason: every trade is reviewed by a human before
this module ever runs, at Control Bot Approve time.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.audit import append_audit_event
from telegram_trader.binance_market_data import get_symbol_filters, round_price, round_quantity
from telegram_trader.binance_trading_client import BinanceApiError
from telegram_trader.config import Settings
from telegram_trader.models import (
    ExchangeOrder,
    Fill,
    OrderLifecycle,
    PositionSnapshot,
    ProtectionOrder,
    RiskDecision,
    TakeProfitOrder,
    TradeIntent,
)
from telegram_trader.outbox import append_outbox_event
from telegram_trader.risk_engine import REASON_MAX_POSITIONS_REACHED, REASON_SYMBOL_CONFLICT

LOGGER = logging.getLogger(__name__)

_ISOLATED_MARGIN_TYPE = "ISOLATED"
_ORDER_DOES_NOT_EXIST = -2013
_PURPOSE_PREFIXES = {
    "ENTRY": "en",
    "PROTECTION": "pr",
    "EMERGENCY_CLOSE": "ec",
    "TAKE_PROFIT": "tp",
}


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    intent_id: str
    symbol: str
    final_state: str
    detail: str


def entry_side_for(side: str) -> str:
    """`LONG` opens by buying, `SHORT` opens by selling -- domain side to Binance side."""
    return "BUY" if side == "LONG" else "SELL"


def close_side_for(side: str) -> str:
    """The opposite of `entry_side_for` -- a `reduceOnly` order always trades the

    other direction from the position it protects/closes.
    """
    return "SELL" if side == "LONG" else "BUY"


def client_order_id_for(intent_id: str, purpose: str) -> str:
    """Deterministic, well under Binance's `clientOrderId`/`clientAlgoId` length

    limits (36 and, empirically, shorter still -- kept to 26 chars total to
    be safe across both). Same `(intent_id, purpose)` always yields the same
    id -- FR-018's idempotency: a retried submission for the same
    intent/purpose reuses the identical id, so `get_order`/`get_algo_order`
    can always resolve whether it already went through before ever placing
    a second one.
    """
    prefix = _PURPOSE_PREFIXES[purpose]
    digest = hashlib.sha256(f"{purpose}:{intent_id}".encode()).hexdigest()[:24]
    return f"{prefix}{digest}"


def _lifecycle_row_id(intent_id: str, revision: int) -> str:
    return hashlib.sha256(f"order_lifecycle:{intent_id}:{revision}".encode()).hexdigest()


def _order_row_id(client_order_id: str) -> str:
    return hashlib.sha256(f"exchange_order:{client_order_id}".encode()).hexdigest()


def _protection_row_id(intent_id: str) -> str:
    return hashlib.sha256(f"protection_order:{intent_id}".encode()).hexdigest()


def _take_profit_row_id(intent_id: str) -> str:
    return hashlib.sha256(f"take_profit_order:{intent_id}".encode()).hexdigest()


def _fill_row_id(order_row_id: str, exchange_trade_id: int) -> str:
    return hashlib.sha256(f"fill:{order_row_id}:{exchange_trade_id}".encode()).hexdigest()


def _position_snapshot_row_id(symbol: str, fetched_at: datetime) -> str:
    canonical = f"position_snapshot:{symbol}:{fetched_at.isoformat()}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def _audit_event_id(intent_id: str, event_type: str, now: datetime) -> str:
    return hashlib.sha256(f"audit:{intent_id}:{event_type}:{now.isoformat()}".encode()).hexdigest()


def load_eligible_intents(session: Session) -> list[tuple[TradeIntent, RiskDecision]]:
    """Every `RISK_APPROVED` intent with no `OrderLifecycle` row yet -- first

    attempt only. A LIMIT entry that never fills within one run's poll
    window is left at `state='SUBMITTED'` and is *not* picked up again by
    a later run in this slice -- FR-020/reconciliation is out of scope,
    see docs/phase-6/known-issues.md.
    """
    already_has_lifecycle = select(OrderLifecycle.intent_id)
    stmt = (
        select(TradeIntent, RiskDecision)
        .join(RiskDecision, RiskDecision.intent_id == TradeIntent.intent_id)
        .where(
            TradeIntent.status == "RISK_APPROVED",
            TradeIntent.intent_id.not_in(already_has_lifecycle),
        )
        .order_by(TradeIntent.created_at)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def check_advisory_conflicts(
    position_rows: list[dict[str, Any]],
    *,
    symbol: str,
    side: str,
    max_concurrent_positions: int,
) -> list[str]:
    """Real-data BR-007/BR-010 recheck -- never blocks (see module docstring).

    `position_rows` is `BinanceTradingClient.get_position_risk()`'s raw
    payload for *all* symbols (mirrors `risk_engine`'s `AccountState`
    fields, just sourced from real Binance data instead of Slice 1's
    clean-slate approximation).
    """
    advisories: list[str] = []
    open_positions = [row for row in position_rows if Decimal(str(row["positionAmt"])) != 0]
    if len(open_positions) >= max_concurrent_positions:
        advisories.append(REASON_MAX_POSITIONS_REACHED)
    for row in open_positions:
        if row["symbol"] != symbol:
            continue
        existing_side = "LONG" if Decimal(str(row["positionAmt"])) > 0 else "SHORT"
        if existing_side != side:
            advisories.append(REASON_SYMBOL_CONFLICT)
    return advisories


def _write_position_snapshot(
    session: Session,
    position_rows: list[dict[str, Any]],
    *,
    intent_id: str,
    symbol: str,
    fetched_at: datetime,
) -> None:
    for row in position_rows:
        if row["symbol"] != symbol:
            continue
        session.execute(
            pg_insert(PositionSnapshot)
            .values(
                snapshot_row_id=_position_snapshot_row_id(row["symbol"], fetched_at),
                environment="TESTNET",
                symbol=row["symbol"],
                position_side=row.get("positionSide", "BOTH"),
                position_amount=Decimal(str(row["positionAmt"])),
                entry_price=Decimal(str(row["entryPrice"])) if row.get("entryPrice") else None,
                mark_price=Decimal(str(row["markPrice"])) if row.get("markPrice") else None,
                unrealized_pnl=(
                    Decimal(str(row["unRealizedProfit"])) if row.get("unRealizedProfit") else None
                ),
                leverage=int(row["leverage"]) if row.get("leverage") else None,
                margin_type=(str(row["marginType"]).upper() if row.get("marginType") else None),
                liquidation_price=(
                    Decimal(str(row["liquidationPrice"])) if row.get("liquidationPrice") else None
                ),
                triggered_by_intent_id=intent_id,
                raw_response=row,
                fetched_at=fetched_at,
            )
            .on_conflict_do_nothing(index_elements=["snapshot_row_id"])
        )


def _write_lifecycle(
    session: Session,
    *,
    intent_id: str,
    decision_id: str,
    revision: int,
    symbol: str,
    side: str,
    entry_client_order_id: str,
    state: str,
    correlation_id: str,
    transition_reason: str | None = None,
    protection_deadline: datetime | None = None,
    now: datetime,
) -> bool:
    """Returns whether this call inserted the row. Revision 0 doubles as the
    per-intent execution claim: `False` there means another run already owns it.
    """
    inserted = session.execute(
        pg_insert(OrderLifecycle)
        .values(
            lifecycle_row_id=_lifecycle_row_id(intent_id, revision),
            intent_id=intent_id,
            revision=revision,
            decision_id=decision_id,
            environment="TESTNET",
            symbol=symbol,
            side=side,
            entry_client_order_id=entry_client_order_id,
            state=state,
            transition_reason=transition_reason,
            protection_deadline=protection_deadline,
            correlation_id=correlation_id,
            transitioned_at=now,
        )
        .on_conflict_do_nothing(index_elements=["intent_id", "revision"])
        .returning(OrderLifecycle.lifecycle_row_id)
    ).first()
    return inserted is not None


def _write_exchange_order(
    session: Session,
    *,
    order_row_id: str,
    intent_id: str,
    purpose: str,
    client_order_id: str,
    symbol: str,
    side: str,
    order_type: str,
    reduce_only: bool,
    requested_quantity: Decimal | None,
    requested_price: Decimal | None,
    requested_stop_price: Decimal | None,
    working_type: str | None,
    status: str,
    exchange_order_id: int | None,
    filled_quantity: Decimal,
    avg_fill_price: Decimal | None,
    raw_response: dict[str, Any] | None,
    submitted_at: datetime | None,
    now: datetime,
) -> None:
    session.execute(
        pg_insert(ExchangeOrder)
        .values(
            order_row_id=order_row_id,
            intent_id=intent_id,
            purpose=purpose,
            client_order_id=client_order_id,
            exchange_order_id=exchange_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            position_side="BOTH",
            reduce_only=reduce_only,
            close_position=False,
            working_type=working_type,
            requested_quantity=requested_quantity,
            requested_price=requested_price,
            requested_stop_price=requested_stop_price,
            status=status,
            filled_quantity=filled_quantity,
            avg_fill_price=avg_fill_price,
            raw_response=raw_response,
            submitted_at=submitted_at,
            last_checked_at=now,
        )
        .on_conflict_do_update(
            index_elements=["order_row_id"],
            set_={
                "exchange_order_id": exchange_order_id,
                "status": status,
                "filled_quantity": filled_quantity,
                "avg_fill_price": avg_fill_price,
                "raw_response": raw_response,
                "last_checked_at": now,
            },
        )
    )


def _record_fills(session: Session, order_row_id: str, trades: list[dict[str, Any]]) -> None:
    for trade in trades:
        session.execute(
            pg_insert(Fill)
            .values(
                fill_row_id=_fill_row_id(order_row_id, int(trade["id"])),
                order_row_id=order_row_id,
                exchange_trade_id=int(trade["id"]),
                quantity=Decimal(str(trade["qty"])),
                price=Decimal(str(trade["price"])),
                commission=Decimal(str(trade.get("commission", "0"))),
                commission_asset=str(trade.get("commissionAsset", "")),
                realized_pnl=(
                    Decimal(str(trade["realizedPnl"])) if trade.get("realizedPnl") else None
                ),
                filled_at=datetime.fromtimestamp(int(trade["time"]) / 1000, tz=UTC),
            )
            .on_conflict_do_nothing(index_elements=["order_row_id", "exchange_trade_id"])
        )


def _outbox_event_id(intent_id: str, event_type: str, now: datetime) -> str:
    return hashlib.sha256(f"outbox:{intent_id}:{event_type}:{now.isoformat()}".encode()).hexdigest()


def _notify(
    session: Session, *, intent_id: str, event_type: str, payload: dict[str, Any], now: datetime
) -> None:
    """Every notable outcome writes both an audit event (FR-022, permanent record)

    and an outbox event (delivery mechanism) -- `control_bot.py` polls the
    latter and forwards each as a Telegram message. Execution Gateway has
    no Telegram credential of its own (credential-handoff.md's boundary is
    unchanged); the outbox table is the decoupling point, reusing Phase 1's
    existing generic outbox infrastructure rather than a new one.

    `trade_intent` has a hard `BEFORE UPDATE` trigger (migration `0014`,
    `reject_trade_intent_update`) making it genuinely append-only --
    confirmed live 2026-09-28 by a real crash, not a design choice. Neither
    call here touches `trade_intent`; `OrderLifecycle`'s own presence/state
    (via `load_eligible_intents`'s "no lifecycle row yet" check) is this
    module's actual source of truth for "has this intent been acted on".
    """
    append_audit_event(
        session,
        event_id=_audit_event_id(intent_id, event_type, now),
        event_type=event_type,
        aggregate_type="trade_intent",
        aggregate_id=intent_id,
        payload=payload,
    )
    append_outbox_event(
        session,
        event_id=_outbox_event_id(intent_id, event_type, now),
        event_type=event_type,
        aggregate_type="trade_intent",
        aggregate_id=intent_id,
        payload=payload,
    )


def _place_order_idempotent(
    trading_client: Any, symbol: str, client_order_id: str, **params: Any
) -> dict[str, Any]:
    """FR-018: on any exception placing the order, query by `client_order_id`

    before ever retrying -- never blind-retry a POST that may have already
    succeeded server-side.

    Also queries *before* the first POST (P6-NOTICE-013): Binance only
    deduplicates `newClientOrderId` while the original order is still open,
    so resubmitting after a MARKET entry has already filled opens a second,
    real position. Any existing order with this id -- in any status, filled
    included -- is adopted instead of placing another. Only `-2013` ("Order
    does not exist") permits the POST; any other lookup failure propagates,
    since not knowing is never a reason to place.
    """
    try:
        existing_before: dict[str, Any] = trading_client.get_order(
            symbol, orig_client_order_id=client_order_id
        )
    except BinanceApiError as error:
        if error.code != _ORDER_DOES_NOT_EXIST:
            raise
    else:
        LOGGER.warning(
            "order with this client_order_id already exists; adopting it, not placing another",
            extra={"context": {"symbol": symbol, "client_order_id": client_order_id}},
        )
        return existing_before
    try:
        result: dict[str, Any] = trading_client.place_order(
            symbol=symbol, newClientOrderId=client_order_id, **params
        )
        return result
    except Exception:
        LOGGER.warning(
            "order placement raised; querying by client_order_id before any retry",
            extra={"context": {"symbol": symbol, "client_order_id": client_order_id}},
        )
        existing: dict[str, Any] = trading_client.get_order(
            symbol, orig_client_order_id=client_order_id
        )
        return existing


def _poll_for_fill(
    trading_client: Any,
    symbol: str,
    client_order_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    deadline = now_monotonic() + timeout_seconds
    order: dict[str, Any] = trading_client.get_order(symbol, orig_client_order_id=client_order_id)
    while Decimal(str(order.get("executedQty", "0"))) <= 0 and now_monotonic() < deadline:
        sleep(poll_interval_seconds)
        order = trading_client.get_order(symbol, orig_client_order_id=client_order_id)
    return order


_ALGO_TERMINAL_FAILURE_STATUSES = ("REJECTED", "EXPIRED", "CANCELED")


def _place_and_confirm_protection(
    trading_client: Any,
    *,
    symbol: str,
    side: str,
    client_order_id: str,
    quantity: Decimal,
    stop_price: Decimal,
    deadline_monotonic: float,
    poll_interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Places the `STOP_MARKET` protection order via the **Algo Order** endpoint

    and confirms it was accepted (`algoStatus` not `REJECTED`/`EXPIRED`/
    `CANCELED`) before FR-019's deadline. Returns the algo-order payload if
    confirmed, `None` otherwise -- the caller only notifies on `None`
    (2026-09-29: no longer auto-closes, per explicit user request).

    Confirmed live 2026-09-28: the plain `/fapi/v1/order` endpoint rejects
    `type=STOP_MARKET` with `-4120`; `POST /fapi/v1/algoOrder`
    (`algoType=CONDITIONAL`) is the only way to place this order type on
    USDⓈ-M Futures. See `BinanceTradingClient.place_algo_order`'s docstring
    and api-contract-inventory.md's CS-BN-002 correction.
    """
    try:
        order: dict[str, Any] = trading_client.place_algo_order(
            symbol=symbol,
            clientAlgoId=client_order_id,
            side=close_side_for(side),
            type="STOP_MARKET",
            reduceOnly="true",
            quantity=str(quantity),
            triggerPrice=str(stop_price),
            workingType="CONTRACT_PRICE",
        )
    except Exception:
        LOGGER.exception("protection order placement failed", extra={"context": {"symbol": symbol}})
        return None

    while now_monotonic() < deadline_monotonic:
        status = str(order.get("algoStatus", ""))
        if status not in _ALGO_TERMINAL_FAILURE_STATUSES:
            return order
        sleep(poll_interval_seconds)
        order = trading_client.get_algo_order(client_algo_id=client_order_id)
    status = str(order.get("algoStatus", ""))
    return order if status not in _ALGO_TERMINAL_FAILURE_STATUSES else None


def _place_and_confirm_take_profit(
    trading_client: Any,
    *,
    symbol: str,
    side: str,
    client_order_id: str,
    quantity: Decimal,
    target_price: Decimal,
) -> dict[str, Any] | None:
    """Places the `TAKE_PROFIT_MARKET` order via the Algo Order endpoint (added

    2026-09-29 per explicit user request) and does a single immediate
    accepted/not-accepted check -- unlike protection, there is no FR-019-style
    hard deadline or retry-polling loop: missing a take-profit target is a
    missed-profit risk, not a safety risk, so one attempt is enough.
    """
    try:
        order: dict[str, Any] = trading_client.place_algo_order(
            symbol=symbol,
            clientAlgoId=client_order_id,
            side=close_side_for(side),
            type="TAKE_PROFIT_MARKET",
            reduceOnly="true",
            quantity=str(quantity),
            triggerPrice=str(target_price),
            workingType="CONTRACT_PRICE",
        )
    except Exception:
        LOGGER.exception(
            "take-profit order placement failed", extra={"context": {"symbol": symbol}}
        )
        return None
    status = str(order.get("algoStatus", ""))
    return order if status not in _ALGO_TERMINAL_FAILURE_STATUSES else None


def _emergency_close(
    trading_client: Any, *, symbol: str, side: str, client_order_id: str, quantity: Decimal
) -> dict[str, Any]:
    result: dict[str, Any] = trading_client.place_order(
        symbol=symbol,
        newClientOrderId=client_order_id,
        side=close_side_for(side),
        type="MARKET",
        reduceOnly="true",
        quantity=str(quantity),
    )
    return result


def _execute_one(
    session_factory: sessionmaker[Session],
    trade_intent: TradeIntent,
    risk_decision: RiskDecision,
    *,
    settings: Settings,
    market_data_client: Any,
    trading_client: Any,
    clock: Callable[[], datetime],
    poll_timeout_seconds: float,
    poll_interval_seconds: float,
    protection_deadline_seconds: float,
) -> ExecutionResult:
    intent_id = trade_intent.intent_id
    symbol = trade_intent.symbol
    side = trade_intent.side
    now = clock()
    correlation_id = hashlib.sha256(f"correlation:{intent_id}".encode()).hexdigest()
    entry_client_order_id = client_order_id_for(intent_id, "ENTRY")

    if risk_decision.expires_at <= now:
        with session_factory.begin() as session:
            _notify(
                session,
                intent_id=intent_id,
                event_type="execution_skipped_expired",
                payload={"symbol": symbol, "expires_at": risk_decision.expires_at.isoformat()},
                now=now,
            )
        return ExecutionResult(
            intent_id, symbol, "EXPIRED", "risk_decision expired before execution"
        )

    # An APPROVED risk_decision always has these populated (risk_engine.evaluate_trade_intent
    # never returns verdict="APPROVED" with any of them None) -- asserted, not silently
    # coerced, so a genuine data-integrity bug fails loudly here rather than downstream.
    if (
        risk_decision.quantity is None
        or risk_decision.entry_price_used is None
        or risk_decision.computed_stop_price is None
    ):
        raise ValueError(
            f"RISK_APPROVED risk_decision {risk_decision.decision_id!r} is missing "
            "quantity/entry_price_used/computed_stop_price"
        )
    approved_quantity: Decimal = risk_decision.quantity
    approved_entry_price: Decimal = risk_decision.entry_price_used
    approved_stop_price: Decimal = risk_decision.computed_stop_price

    with session_factory.begin() as session:
        claimed = _write_lifecycle(
            session,
            intent_id=intent_id,
            decision_id=risk_decision.decision_id,
            revision=0,
            symbol=symbol,
            side=side,
            entry_client_order_id=entry_client_order_id,
            state="PENDING_SUBMIT",
            correlation_id=correlation_id,
            now=now,
        )
    if not claimed:
        # A concurrent run inserted revision 0 between our load_eligible_intents
        # and here; it owns this intent. Proceeding would submit the entry twice.
        return ExecutionResult(
            intent_id, symbol, "SKIPPED", "intent already claimed by another execution run"
        )

    # --- Step 2: real account/position preflight (BR-007/010, advisory only) ---
    all_positions = trading_client.get_position_risk()
    with session_factory.begin() as session:
        _write_position_snapshot(
            session, all_positions, intent_id=intent_id, symbol=symbol, fetched_at=now
        )
        advisories = check_advisory_conflicts(
            all_positions,
            symbol=symbol,
            side=side,
            max_concurrent_positions=settings.risk_max_concurrent_positions,
        )
        if advisories:
            append_audit_event(
                session,
                event_id=_audit_event_id(intent_id, "execution_advisory", now),
                event_type="execution_advisory",
                aggregate_type="trade_intent",
                aggregate_id=intent_id,
                payload={"advisory_codes": advisories},
            )

    # --- Step 3: ensure symbol mode ---
    trading_client.set_leverage(symbol, settings.risk_leverage)
    trading_client.set_margin_type(symbol, _ISOLATED_MARGIN_TYPE)

    # --- Step 4: round to exchange filters ---
    exchange_info = market_data_client.get_exchange_info()
    filters = get_symbol_filters(exchange_info, symbol)
    quantity = round_quantity(approved_quantity, filters)
    if quantity <= 0:
        with session_factory.begin() as session:
            _notify(
                session,
                intent_id=intent_id,
                event_type="execution_cancelled",
                payload={"symbol": symbol, "reason": "rounded quantity is not positive"},
                now=clock(),
            )
        return ExecutionResult(intent_id, symbol, "CANCELLED", "rounded quantity is not positive")

    entry_order_params: dict[str, Any] = {"side": entry_side_for(side)}
    if trade_intent.entry_type == "MARKET":
        entry_order_params["type"] = "MARKET"
    else:
        entry_price = round_price(approved_entry_price, filters)
        entry_order_params.update(type="LIMIT", timeInForce="GTC", price=str(entry_price))
    entry_order_params["quantity"] = str(quantity)

    # --- Step 5: place entry order (idempotent) ---
    entry_response = _place_order_idempotent(
        trading_client, symbol, entry_client_order_id, **entry_order_params
    )
    entry_order_row_id = _order_row_id(entry_client_order_id)
    with session_factory.begin() as session:
        _write_exchange_order(
            session,
            order_row_id=entry_order_row_id,
            intent_id=intent_id,
            purpose="ENTRY",
            client_order_id=entry_client_order_id,
            symbol=symbol,
            side=entry_order_params["side"],
            order_type=entry_order_params["type"],
            reduce_only=False,
            requested_quantity=quantity,
            requested_price=(
                Decimal(entry_order_params["price"]) if "price" in entry_order_params else None
            ),
            requested_stop_price=None,
            working_type=None,
            status=str(entry_response.get("status", "UNKNOWN")),
            exchange_order_id=entry_response.get("orderId"),
            filled_quantity=Decimal(str(entry_response.get("executedQty", "0"))),
            avg_fill_price=(
                Decimal(str(entry_response["avgPrice"])) if entry_response.get("avgPrice") else None
            ),
            raw_response=entry_response,
            submitted_at=now,
            now=now,
        )
        _write_lifecycle(
            session,
            intent_id=intent_id,
            decision_id=risk_decision.decision_id,
            revision=1,
            symbol=symbol,
            side=side,
            entry_client_order_id=entry_client_order_id,
            state="SUBMITTED",
            correlation_id=correlation_id,
            transition_reason="entry_order_submitted",
            now=clock(),
        )
        _notify(
            session,
            intent_id=intent_id,
            event_type="execution_entry_submitted",
            payload={
                "symbol": symbol,
                "side": side,
                "entry_type": entry_order_params["type"],
                "quantity": str(quantity),
                "price": entry_order_params.get("price"),
                "status": str(entry_response.get("status", "UNKNOWN")),
            },
            now=clock(),
        )

    # --- Step 6: poll for a confirmed real fill ---
    filled_order = _poll_for_fill(
        trading_client,
        symbol,
        entry_client_order_id,
        timeout_seconds=poll_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    filled_quantity = Decimal(str(filled_order.get("executedQty", "0")))
    with session_factory.begin() as session:
        _write_exchange_order(
            session,
            order_row_id=entry_order_row_id,
            intent_id=intent_id,
            purpose="ENTRY",
            client_order_id=entry_client_order_id,
            symbol=symbol,
            side=entry_order_params["side"],
            order_type=entry_order_params["type"],
            reduce_only=False,
            requested_quantity=quantity,
            requested_price=(
                Decimal(entry_order_params["price"]) if "price" in entry_order_params else None
            ),
            requested_stop_price=None,
            working_type=None,
            status=str(filled_order.get("status", "UNKNOWN")),
            exchange_order_id=filled_order.get("orderId"),
            filled_quantity=filled_quantity,
            avg_fill_price=(
                Decimal(str(filled_order["avgPrice"])) if filled_order.get("avgPrice") else None
            ),
            raw_response=filled_order,
            submitted_at=now,
            now=clock(),
        )

    if filled_quantity <= 0:
        with session_factory.begin() as session:
            _write_lifecycle(
                session,
                intent_id=intent_id,
                decision_id=risk_decision.decision_id,
                revision=2,
                symbol=symbol,
                side=side,
                entry_client_order_id=entry_client_order_id,
                state="SUBMITTED",
                correlation_id=correlation_id,
                transition_reason="entry_not_filled_within_timeout",
                now=clock(),
            )
            _notify(
                session,
                intent_id=intent_id,
                event_type="execution_entry_not_filled",
                payload={"symbol": symbol, "status": str(filled_order.get("status", "UNKNOWN"))},
                now=clock(),
            )
        return ExecutionResult(
            intent_id, symbol, "SUBMITTED", "entry order did not fill within timeout"
        )

    fill_confirmed_at = clock()
    with session_factory.begin() as session:
        _record_fills(session, entry_order_row_id, [filled_order]) if filled_order.get(
            "id"
        ) else None
        _write_lifecycle(
            session,
            intent_id=intent_id,
            decision_id=risk_decision.decision_id,
            revision=2,
            symbol=symbol,
            side=side,
            entry_client_order_id=entry_client_order_id,
            state="FILLED",
            correlation_id=correlation_id,
            transition_reason="entry_fill_confirmed",
            protection_deadline=fill_confirmed_at,
            now=fill_confirmed_at,
        )
        _notify(
            session,
            intent_id=intent_id,
            event_type="execution_entry_filled",
            payload={
                "symbol": symbol,
                "side": side,
                "filled_quantity": str(filled_quantity),
                "avg_fill_price": filled_order.get("avgPrice"),
            },
            now=fill_confirmed_at,
        )

    # --- Step 7: protection order within FR-019's 5-second deadline ---
    protection_client_order_id = client_order_id_for(intent_id, "PROTECTION")
    protection_row_id = _protection_row_id(intent_id)
    deadline_monotonic = time.monotonic() + protection_deadline_seconds
    with session_factory.begin() as session:
        session.execute(
            pg_insert(ProtectionOrder)
            .values(
                protection_row_id=protection_row_id,
                intent_id=intent_id,
                exchange_order_id=None,
                protected_quantity=filled_quantity,
                stop_price=approved_stop_price,
                state="PENDING",
                confirmation_deadline=fill_confirmed_at,
            )
            .on_conflict_do_nothing(index_elements=["intent_id"])
        )

    protection_order = _place_and_confirm_protection(
        trading_client,
        symbol=symbol,
        side=side,
        client_order_id=protection_client_order_id,
        quantity=filled_quantity,
        stop_price=approved_stop_price,
        deadline_monotonic=deadline_monotonic,
        poll_interval_seconds=min(poll_interval_seconds, 1.0),
    )

    if protection_order is not None:
        protection_order_row_id = _order_row_id(protection_client_order_id)
        with session_factory.begin() as session:
            _write_exchange_order(
                session,
                order_row_id=protection_order_row_id,
                intent_id=intent_id,
                purpose="PROTECTION",
                client_order_id=protection_client_order_id,
                symbol=symbol,
                side=close_side_for(side),
                order_type="STOP_MARKET",
                reduce_only=True,
                requested_quantity=filled_quantity,
                requested_price=None,
                requested_stop_price=approved_stop_price,
                working_type="CONTRACT_PRICE",
                status=str(protection_order.get("algoStatus", "UNKNOWN")),
                exchange_order_id=protection_order.get("algoId"),
                filled_quantity=Decimal("0"),
                avg_fill_price=None,
                raw_response=protection_order,
                submitted_at=clock(),
                now=clock(),
            )
            session.execute(
                update(ProtectionOrder)
                .where(ProtectionOrder.intent_id == intent_id)
                .values(
                    exchange_order_id=protection_order_row_id,
                    state="CONFIRMED",
                    confirmed_at=clock(),
                )
            )
            _write_lifecycle(
                session,
                intent_id=intent_id,
                decision_id=risk_decision.decision_id,
                revision=3,
                symbol=symbol,
                side=side,
                entry_client_order_id=entry_client_order_id,
                state="PROTECTED",
                correlation_id=correlation_id,
                transition_reason="protection_order_confirmed",
                now=clock(),
            )
            _notify(
                session,
                intent_id=intent_id,
                event_type="execution_protection_confirmed",
                payload={
                    "symbol": symbol,
                    "quantity": str(filled_quantity),
                    "stop_price": str(approved_stop_price),
                },
                now=clock(),
            )
        protection_summary = f"stop confirmed @ {approved_stop_price}"
    else:
        # No automatic Emergency Close (2026-09-29, explicit user request): notify only,
        # the position stays open and unprotected until the user sets a stop themselves.
        LOGGER.error(
            "protection order not confirmed within FR-019 deadline; notifying, not closing",
            extra={"context": {"intent_id": intent_id, "symbol": symbol}},
        )
        with session_factory.begin() as session:
            session.execute(
                update(ProtectionOrder)
                .where(ProtectionOrder.intent_id == intent_id)
                .values(state="FAILED")
            )
            _write_lifecycle(
                session,
                intent_id=intent_id,
                decision_id=risk_decision.decision_id,
                revision=3,
                symbol=symbol,
                side=side,
                entry_client_order_id=entry_client_order_id,
                state="PROTECTION_FAILED",
                correlation_id=correlation_id,
                transition_reason="protection_order_unconfirmed",
                now=clock(),
            )
            _notify(
                session,
                intent_id=intent_id,
                event_type="execution_protection_failed",
                payload={
                    "symbol": symbol,
                    "side": side,
                    "quantity": str(filled_quantity),
                    "stop_price": str(approved_stop_price),
                },
                now=clock(),
            )
        protection_summary = "stop FAILED -- position is UNPROTECTED, set a stop yourself"

    # --- Take-profit (added 2026-09-29, explicit user request): best-effort, independent
    # of the protection outcome above -- only the first take_profits value is used. ---
    take_profit_summary = "no take-profit requested"
    if trade_intent.take_profits:
        target_price = round_price(Decimal(str(trade_intent.take_profits[0])), filters)
        take_profit_client_order_id = client_order_id_for(intent_id, "TAKE_PROFIT")
        take_profit_order = _place_and_confirm_take_profit(
            trading_client,
            symbol=symbol,
            side=side,
            client_order_id=take_profit_client_order_id,
            quantity=filled_quantity,
            target_price=target_price,
        )
        with session_factory.begin() as session:
            session.execute(
                pg_insert(TakeProfitOrder)
                .values(
                    take_profit_row_id=_take_profit_row_id(intent_id),
                    intent_id=intent_id,
                    exchange_order_id=None,
                    target_quantity=filled_quantity,
                    target_price=target_price,
                    state="CONFIRMED" if take_profit_order is not None else "FAILED",
                    confirmed_at=clock() if take_profit_order is not None else None,
                )
                .on_conflict_do_nothing(index_elements=["intent_id"])
            )
            if take_profit_order is not None:
                _write_exchange_order(
                    session,
                    order_row_id=_order_row_id(take_profit_client_order_id),
                    intent_id=intent_id,
                    purpose="TAKE_PROFIT",
                    client_order_id=take_profit_client_order_id,
                    symbol=symbol,
                    side=close_side_for(side),
                    order_type="TAKE_PROFIT_MARKET",
                    reduce_only=True,
                    requested_quantity=filled_quantity,
                    requested_price=None,
                    requested_stop_price=target_price,
                    working_type="CONTRACT_PRICE",
                    status=str(take_profit_order.get("algoStatus", "UNKNOWN")),
                    exchange_order_id=take_profit_order.get("algoId"),
                    filled_quantity=Decimal("0"),
                    avg_fill_price=None,
                    raw_response=take_profit_order,
                    submitted_at=clock(),
                    now=clock(),
                )
                take_profit_summary = f"take-profit confirmed @ {target_price}"
            else:
                take_profit_summary = f"take-profit FAILED @ {target_price}"
            _notify(
                session,
                intent_id=intent_id,
                event_type=(
                    "execution_take_profit_confirmed"
                    if take_profit_order is not None
                    else "execution_take_profit_failed"
                ),
                payload={
                    "symbol": symbol,
                    "quantity": str(filled_quantity),
                    "target_price": str(target_price),
                },
                now=clock(),
            )

    final_state = "PROTECTED" if protection_order is not None else "PROTECTION_FAILED"
    detail = f"{protection_summary}; {take_profit_summary}"
    return ExecutionResult(intent_id, symbol, final_state, detail)


def run_execution(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    market_data_client: Any,
    trading_client: Any,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    poll_timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
    protection_deadline_seconds: float = 5.0,
) -> list[ExecutionResult]:
    """Process every eligible `RISK_APPROVED` intent to a terminal state, one at a time.

    An unexpected exception in one intent's execution is caught, notified
    (2026-09-29: "success or failure must always send a message"), and does
    not stop the rest of the batch -- a crash on intent A must not silently
    skip notifying about, or processing, intent B.
    """
    with session_factory() as session:
        eligible = load_eligible_intents(session)

    results: list[ExecutionResult] = []
    for trade_intent, risk_decision in eligible:
        try:
            results.append(
                _execute_one(
                    session_factory,
                    trade_intent,
                    risk_decision,
                    settings=settings,
                    market_data_client=market_data_client,
                    trading_client=trading_client,
                    clock=clock,
                    poll_timeout_seconds=poll_timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                    protection_deadline_seconds=protection_deadline_seconds,
                )
            )
        except Exception as error:
            LOGGER.exception(
                "unexpected error executing intent",
                extra={"context": {"intent_id": trade_intent.intent_id}},
            )
            with session_factory.begin() as session:
                _notify(
                    session,
                    intent_id=trade_intent.intent_id,
                    event_type="execution_unexpected_error",
                    payload={"symbol": trade_intent.symbol, "error": repr(error)},
                    now=clock(),
                )
            results.append(
                ExecutionResult(trade_intent.intent_id, trade_intent.symbol, "ERROR", repr(error))
            )
    return results
