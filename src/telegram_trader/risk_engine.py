"""Deterministic Risk Engine (FR-016, Phase 6 Slice 1).

Pure evaluation logic only -- no Telegram, no AI, no Binance credential.
Per architecture.md's module boundary ("risk-engine ... must not import AI
or Telegram adapters"), this module imports nothing from `control_bot.py`,
`telegram_collector.py`, `thesis_extraction.py`, or `openai_client.py`, and
never will.

Honest Slice-1 limitation, repeated here deliberately rather than buried in
one docstring: `AccountState` (equity, open positions, daily loss) is a
*configured* input, not real Binance account data -- no Execution Gateway
exists yet to query one (Slice 2's job). Every `RiskDecision` this module
produces is fully deterministic and replayable given its recorded inputs,
but those inputs are only as true as whatever `AccountState` was supplied.
BR-010's "existing manual positions also count" cannot actually be
satisfied until Slice 2 lands.

TBD-003 (system-spec.md): the ROE-to-stop-price formula implemented here
(`compute_roe_stop_price`) is the baseline leverage-only formula. It does
not account for fees, funding, maintenance margin, or a slippage buffer --
those remain genuinely unresolved, not silently assumed away. See
docs/phase-6/known-issues.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

Side = str  # "LONG" | "SHORT"
EntryType = str  # "MARKET" | "LIMIT" | "RANGE"
Verdict = str  # "APPROVED" | "REJECTED"

REASON_STALE_MARKET_DATA = "STALE_MARKET_DATA"
REASON_SOURCE_TOO_OLD = "SOURCE_TOO_OLD"
REASON_RECEIVE_LAG_TOO_HIGH = "RECEIVE_LAG_TOO_HIGH"
REASON_DAILY_LOSS_KILL_SWITCH = "DAILY_LOSS_KILL_SWITCH"
REASON_SYMBOL_CONFLICT = "SYMBOL_CONFLICT"
REASON_MAX_POSITIONS_REACHED = "MAX_POSITIONS_REACHED"
REASON_INVALID_STOP = "INVALID_STOP"
REASON_QUANTITY_NOT_POSITIVE = "QUANTITY_NOT_POSITIVE"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """One `risk_config_snapshot` row's values, as a plain dataclass for pure evaluation."""

    leverage: int
    default_stop_roe_pct: Decimal
    max_single_trade_risk_pct: Decimal
    max_single_trade_initial_margin_pct: Decimal
    max_total_initial_margin_pct: Decimal
    max_concurrent_positions: int
    daily_loss_kill_switch_pct: Decimal
    max_source_age_seconds: int
    max_receive_lag_seconds: int
    max_price_deviation_bps: int
    equity_baseline_usdt: Decimal


@dataclass(frozen=True, slots=True)
class AccountState:
    """Slice-1 limitation: configured/self-reported, not queried from a real account.

    `open_position_sides` maps symbol -> current side, for BR-007's
    same-symbol-reverse conflict check. `total_initial_margin_used_usdt` is
    the sum of margin already committed by other open positions.
    `daily_realized_loss_pct` is negative for a loss (e.g. `-0.02` = -2%),
    scoped to the current Asia/Taipei trading day by whatever supplies it.
    """

    equity_usdt: Decimal
    open_position_count: int
    open_position_sides: dict[str, Side]
    total_initial_margin_used_usdt: Decimal
    daily_realized_loss_pct: Decimal


@dataclass(frozen=True, slots=True)
class TradeIntentInput:
    symbol: str
    side: Side
    entry_type: EntryType
    entry_values: list[Decimal]
    stop_value: Decimal | None
    signal_source_date: datetime
    signal_received_at: datetime


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    verdict: Verdict
    reason_codes: list[str]
    computed_stop_price: Decimal | None
    quantity: Decimal | None
    entry_price_used: Decimal | None


def compute_roe_stop_price(
    entry_price: Decimal, side: Side, *, leverage: int, roe_pct: Decimal
) -> Decimal:
    """TBD-003 baseline formula: leverage-only, no fees/funding/slippage buffer.

    ROE = leverage * (price_change / entry_price) * (+1 for LONG, -1 for SHORT).
    Solving ROE = -roe_pct for the stop price:
    LONG:  stop = entry * (1 - roe_pct / leverage)
    SHORT: stop = entry * (1 + roe_pct / leverage)
    """
    fraction = roe_pct / Decimal(leverage)
    if side == "LONG":
        return entry_price * (Decimal(1) - fraction)
    return entry_price * (Decimal(1) + fraction)


def _entry_price_used(intent: TradeIntentInput, market_price: Decimal) -> Decimal:
    """MARKET uses the live mark price; LIMIT/RANGE use the signal's own entry value(s)."""
    if intent.entry_type == "MARKET" or not intent.entry_values:
        return market_price
    if intent.entry_type == "RANGE" and len(intent.entry_values) >= 2:
        return (intent.entry_values[0] + intent.entry_values[1]) / Decimal(2)
    return intent.entry_values[0]


def _is_stop_valid(stop_price: Decimal, side: Side, entry_price: Decimal) -> bool:
    if side == "LONG":
        return stop_price < entry_price
    return stop_price > entry_price


def _sized_quantity(
    *,
    equity: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    leverage: int,
    existing_total_margin: Decimal,
    config: RiskConfig,
) -> Decimal:
    """The most conservative of three independent caps (BR-009): risk-based sizing,

    single-trade margin cap, and remaining total-margin headroom. `min()`
    across all three, never a silent average or a reject-only response --
    "限制" (limit) in FR-016's own wording implies capping, not refusing a
    trade that a smaller size would make acceptable.
    """
    risk_amount = equity * config.max_single_trade_risk_pct
    stop_distance = abs(entry_price - stop_price)
    risk_capped_qty = risk_amount / stop_distance if stop_distance > 0 else Decimal(0)

    single_margin_cap_usdt = equity * config.max_single_trade_initial_margin_pct
    single_margin_capped_qty = (single_margin_cap_usdt * Decimal(leverage)) / entry_price

    total_margin_cap_usdt = equity * config.max_total_initial_margin_pct
    remaining_margin_usdt = total_margin_cap_usdt - existing_total_margin
    remaining_margin_capped_qty = (
        (remaining_margin_usdt * Decimal(leverage)) / entry_price
        if remaining_margin_usdt > 0
        else Decimal(0)
    )

    return min(risk_capped_qty, single_margin_capped_qty, remaining_margin_capped_qty)


def evaluate_trade_intent(
    intent: TradeIntentInput,
    *,
    config: RiskConfig,
    account: AccountState,
    market_price: Decimal | None,
    now: datetime,
) -> RiskEvaluation:
    """Deterministic, fixed-rule evaluation (FR-016). Never raises for a bad intent --

    every failure mode is an explicit `REJECTED` reason code, not an
    exception, so a caller always gets a recordable `RiskEvaluation`.
    """
    reasons: list[str] = []

    if market_price is None:
        return RiskEvaluation(
            verdict="REJECTED",
            reason_codes=[REASON_STALE_MARKET_DATA],
            computed_stop_price=None,
            quantity=None,
            entry_price_used=None,
        )

    if account.daily_realized_loss_pct <= -config.daily_loss_kill_switch_pct:
        return RiskEvaluation(
            verdict="REJECTED",
            reason_codes=[REASON_DAILY_LOSS_KILL_SWITCH],
            computed_stop_price=None,
            quantity=None,
            entry_price_used=None,
        )

    source_age_seconds = (now - intent.signal_source_date).total_seconds()
    if source_age_seconds > config.max_source_age_seconds:
        reasons.append(REASON_SOURCE_TOO_OLD)
    receive_lag_seconds = (intent.signal_received_at - intent.signal_source_date).total_seconds()
    if receive_lag_seconds > config.max_receive_lag_seconds:
        reasons.append(REASON_RECEIVE_LAG_TOO_HIGH)
    # BR-006's price-deviation leg is not implemented in Slice 1: no reference
    # price is captured at signal-receive time to deviate from -- see
    # docs/phase-6/known-issues.md rather than fabricate a comparison.

    existing_side = account.open_position_sides.get(intent.symbol)
    if existing_side is not None and existing_side != intent.side:
        reasons.append(REASON_SYMBOL_CONFLICT)

    if account.open_position_count >= config.max_concurrent_positions:
        reasons.append(REASON_MAX_POSITIONS_REACHED)

    entry_price = _entry_price_used(intent, market_price)
    stop_price = intent.stop_value
    if stop_price is None:
        stop_price = compute_roe_stop_price(
            entry_price, intent.side, leverage=config.leverage, roe_pct=config.default_stop_roe_pct
        )
    if not _is_stop_valid(stop_price, intent.side, entry_price):
        reasons.append(REASON_INVALID_STOP)

    if reasons:
        return RiskEvaluation(
            verdict="REJECTED",
            reason_codes=reasons,
            computed_stop_price=None,
            quantity=None,
            entry_price_used=None,
        )

    quantity = _sized_quantity(
        equity=account.equity_usdt,
        entry_price=entry_price,
        stop_price=stop_price,
        leverage=config.leverage,
        existing_total_margin=account.total_initial_margin_used_usdt,
        config=config,
    )
    if quantity <= 0:
        return RiskEvaluation(
            verdict="REJECTED",
            reason_codes=[REASON_QUANTITY_NOT_POSITIVE],
            computed_stop_price=None,
            quantity=None,
            entry_price_used=None,
        )

    return RiskEvaluation(
        verdict="APPROVED",
        reason_codes=[],
        computed_stop_price=stop_price,
        quantity=quantity,
        entry_price_used=entry_price,
    )
