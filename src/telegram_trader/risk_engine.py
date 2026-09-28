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
docs/phase-6/known-issues.md. Lower priority since 2026-09-25 (P6-NOTICE-007):
the user's real signals always carry a structure-based `stop_value`, so this
formula only ever runs as a fallback for a signal that omits one, not the
common case.

Sizing/approval policy, 2026-09-25 (P6-NOTICE-007, supersedes P6-LIMIT-005):
every trade is reviewed by the user before placement, so BR-007 (symbol
conflict), BR-010 (max concurrent positions), and BR-011 (daily kill switch)
no longer force a `REJECTED` verdict -- they surface as `advisory_codes`
instead. Sizing (BR-009) is a flat `equity * position_size_pct` margin at
`leverage`, not a function of stop distance, since the user's stop comes
from chart structure, not risk math.

BR-006 freshness, revised (P6-LIMIT-004): a raw elapsed-time check against
the signal's own post time was found to reject nearly every real signal
under this project's still-manual approval workflow (BR-002) -- a human
reasonably takes longer than the original 60-second window to see a
notification and tap Approve, and that delay says nothing about whether
the trade is still safe. Replaced with BR-006's own price-deviation leg
for `MARKET` entries: compare the price near the signal's original post
time against the current price, and reject only if the market actually
moved too much, regardless of how much time passed. A five-minute-old
approval on an unmoved price is fine; a ten-second-old approval after a
sharp move is not. `max_receive_lag_seconds` is unchanged -- that measures
this project's own collector keeping up, an unrelated infrastructure
health question, not the human-approval-delay problem this replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

Side = str  # "LONG" | "SHORT"
EntryType = str  # "MARKET" | "LIMIT" | "RANGE"
Verdict = str  # "APPROVED" | "REJECTED"

REASON_STALE_MARKET_DATA = "STALE_MARKET_DATA"
REASON_SIGNAL_NOT_VALIDATED = "SIGNAL_NOT_VALIDATED"
REASON_REFERENCE_PRICE_UNAVAILABLE = "REFERENCE_PRICE_UNAVAILABLE"
REASON_PRICE_DEVIATION_TOO_HIGH = "PRICE_DEVIATION_TOO_HIGH"
REASON_RECEIVE_LAG_TOO_HIGH = "RECEIVE_LAG_TOO_HIGH"
REASON_DAILY_LOSS_KILL_SWITCH = "DAILY_LOSS_KILL_SWITCH"
REASON_SYMBOL_CONFLICT = "SYMBOL_CONFLICT"
REASON_MAX_POSITIONS_REACHED = "MAX_POSITIONS_REACHED"
REASON_INVALID_STOP = "INVALID_STOP"
REASON_QUANTITY_NOT_POSITIVE = "QUANTITY_NOT_POSITIVE"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """One `risk_config_snapshot` row's values, as a plain dataclass for pure evaluation.

    `position_size_pct` (P6-LIMIT-005, superseded 2026-09-25): sizing is no
    longer the minimum of three independently-computed caps. The user's own
    trading practice sets stop-loss by chart structure (prior swing low/high),
    not by a risk-derived stop distance, so a risk-based sizing cap tied to
    stop distance no longer matches how stops are actually chosen. Sizing is
    now a flat `equity * position_size_pct` margin at `leverage`, full stop.
    See docs/phase-6/known-issues.md P6-NOTICE-007.
    """

    leverage: int
    default_stop_roe_pct: Decimal
    position_size_pct: Decimal
    max_concurrent_positions: int
    daily_loss_kill_switch_pct: Decimal
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
    """`advisory_codes` (added 2026-09-25, see P6-NOTICE-007): BR-007/010/011

    (symbol conflict, max concurrent positions, daily kill switch) are
    surfaced here but never block approval or block computing a stop/
    quantity -- the user reviews every trade before it is placed and wants
    these as visible flags, not automatic rejections. `reason_codes` is
    reserved for what still fails closed: missing/stale market data,
    BR-006 price deviation, receive lag, an invalid stop, or a
    non-positive computed quantity.
    """

    verdict: Verdict
    reason_codes: list[str]
    advisory_codes: list[str] = field(default_factory=list)
    computed_stop_price: Decimal | None = None
    quantity: Decimal | None = None
    entry_price_used: Decimal | None = None


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
    leverage: int,
    config: RiskConfig,
) -> Decimal:
    """Flat sizing (BR-009, superseded 2026-09-25): `equity * position_size_pct`

    as margin, at `leverage`, full stop -- not a function of stop distance.
    See `RiskConfig.position_size_pct`'s docstring for why the old
    three-cap-minimum no longer applies.
    """
    margin_usdt = equity * config.position_size_pct
    notional_usdt = margin_usdt * Decimal(leverage)
    return notional_usdt / entry_price


def evaluate_trade_intent(
    intent: TradeIntentInput,
    *,
    config: RiskConfig,
    account: AccountState,
    market_price: Decimal | None,
    reference_price: Decimal | None,
    now: datetime,
) -> RiskEvaluation:
    """Deterministic, fixed-rule evaluation (FR-016). Never raises for a bad intent --

    every failure mode is an explicit `REJECTED` reason code, not an
    exception, so a caller always gets a recordable `RiskEvaluation`.

    `reference_price` is the price near the signal's original post time
    (BR-006's price-deviation leg for `MARKET` entries only -- see the
    module docstring's "BR-006 freshness, revised" note); ignored for
    `LIMIT`/`RANGE`, which anchor to the signal's own stated price instead
    of live market conditions.

    BR-007/010/011, superseded 2026-09-25 (see P6-NOTICE-007): symbol
    conflict, max concurrent positions, and the daily kill switch no longer
    force a `REJECTED` verdict -- every trade is user-reviewed before
    placement, so these are surfaced as `advisory_codes` instead, visible
    but never silently blocking. `reason_codes` still fails closed for
    market-data/timing/math problems a human reviewing the trade cannot
    reasonably judge from the notification alone.
    """
    reasons: list[str] = []
    advisories: list[str] = []

    if market_price is None:
        return RiskEvaluation(
            verdict="REJECTED",
            reason_codes=[REASON_STALE_MARKET_DATA],
        )

    if account.daily_realized_loss_pct <= -config.daily_loss_kill_switch_pct:
        advisories.append(REASON_DAILY_LOSS_KILL_SWITCH)

    if intent.entry_type == "MARKET":
        if reference_price is None:
            reasons.append(REASON_REFERENCE_PRICE_UNAVAILABLE)
        elif reference_price > 0:
            deviation_bps = abs(market_price - reference_price) / reference_price * Decimal(10000)
            if deviation_bps > config.max_price_deviation_bps:
                reasons.append(REASON_PRICE_DEVIATION_TOO_HIGH)

    receive_lag_seconds = (intent.signal_received_at - intent.signal_source_date).total_seconds()
    if receive_lag_seconds > config.max_receive_lag_seconds:
        reasons.append(REASON_RECEIVE_LAG_TOO_HIGH)

    existing_side = account.open_position_sides.get(intent.symbol)
    if existing_side is not None and existing_side != intent.side:
        advisories.append(REASON_SYMBOL_CONFLICT)

    if account.open_position_count >= config.max_concurrent_positions:
        advisories.append(REASON_MAX_POSITIONS_REACHED)

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
            advisory_codes=advisories,
        )

    quantity = _sized_quantity(
        equity=account.equity_usdt,
        entry_price=entry_price,
        leverage=config.leverage,
        config=config,
    )
    if quantity <= 0:
        return RiskEvaluation(
            verdict="REJECTED",
            reason_codes=[REASON_QUANTITY_NOT_POSITIVE],
            advisory_codes=advisories,
        )

    return RiskEvaluation(
        verdict="APPROVED",
        reason_codes=[],
        advisory_codes=advisories,
        computed_stop_price=stop_price,
        quantity=quantity,
        entry_price_used=entry_price,
    )
