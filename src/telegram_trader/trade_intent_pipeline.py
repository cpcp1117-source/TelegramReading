"""Orchestration: `APPROVED` signal decisions -> `TradeIntent` -> `RiskDecision` (Phase 6 Slice 1).

Mirrors `parse_signals.py`'s relationship to `signal_parser.py`: this module
owns the database queries and side effects, `risk_engine.py` stays pure.

Honest Slice-1 limitation, worth repeating at the orchestration layer where
it actually bites: `default_starting_account_state` always starts a run from
a clean slate (zero open positions, zero margin used, zero daily loss) --
there is no Execution Gateway yet to report real fills/closes, so this
module cannot know what's genuinely still open across separate runs. Within
one run, approved intents *do* accumulate against each other (so a batch of
several signals in the same run won't all pretend the account is still
empty), but that in-memory state is discarded at the end of the run. This
is not a bug -- it is the honest boundary of what's buildable before Slice 2
gives this module real account data to query instead.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.analysis_setup import SETUP_PARSER_VERSION_PREFIX
from telegram_trader.binance_market_data import (
    MarketPriceQuote,
    fetch_mark_price,
    fetch_reference_price,
)
from telegram_trader.config import Settings
from telegram_trader.daily_loss import active_pause, daily_loss_pct
from telegram_trader.models import (
    NormalizedSignal,
    RiskConfigSnapshot,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TelegramMessageVersion,
)
from telegram_trader.models import RiskDecision as RiskDecisionRow
from telegram_trader.models import TradeIntent as TradeIntentRow
from telegram_trader.outbox import append_outbox_event
from telegram_trader.risk_engine import (
    REASON_SIGNAL_NOT_VALIDATED,
    REASON_TRADING_PAUSED,
    AccountState,
    RiskConfig,
    RiskEvaluation,
    TradeIntentInput,
    evaluate_trade_intent,
)
from telegram_trader.signal_decisions import DraftValues

LOGGER = logging.getLogger(__name__)

ADVISORY_ENTRY_FAR_FROM_MARKET = "ENTRY_FAR_FROM_MARKET"
_ENTRY_FAR_FROM_MARKET = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class RiskPipelineResult:
    intent_id: str
    symbol: str
    verdict: str
    reason_codes: list[str]


def _intent_id_for_event(event_id: str) -> str:
    return hashlib.sha256(f"trade_intent:{event_id}".encode()).hexdigest()


def _decision_id_for_intent(intent_id: str) -> str:
    return hashlib.sha256(f"risk_decision:{intent_id}".encode()).hexdigest()


def _config_snapshot_id(values: tuple[object, ...]) -> str:
    canonical = "|".join(str(v) for v in values)
    return hashlib.sha256(canonical.encode()).hexdigest()


def risk_config_from_settings(settings: Settings) -> RiskConfig:
    if settings.risk_equity_baseline_usdt is None:
        raise ValueError(
            "RISK_EQUITY_BASELINE_USDT must be configured before running the Risk Engine "
            "(no default -- see docs/phase-6/known-issues.md)"
        )
    return RiskConfig(
        leverage=settings.risk_leverage,
        default_stop_roe_pct=settings.risk_default_stop_roe_pct,
        position_size_pct=settings.risk_position_size_pct,
        max_concurrent_positions=settings.risk_max_concurrent_positions,
        daily_loss_kill_switch_pct=settings.risk_daily_loss_kill_switch_pct,
        max_receive_lag_seconds=settings.risk_max_receive_lag_seconds,
        max_price_deviation_bps=settings.risk_max_price_deviation_bps,
        equity_baseline_usdt=settings.risk_equity_baseline_usdt,
    )


def default_starting_account_state(
    config: RiskConfig, *, daily_realized_loss_pct: Decimal = Decimal(0)
) -> AccountState:
    """Clean-slate starting point for one run -- see module docstring's Slice-1 caveat.

    `daily_realized_loss_pct` is the only real account input: today's (UTC day)
    recorded net PnL over the equity baseline, from `daily_loss.daily_loss_pct`.
    It only raises the DAILY_LOSS_KILL_SWITCH advisory; the actual stop is the
    user's own Telegram answer (`daily_loss.active_pause`).
    """
    return AccountState(
        equity_usdt=config.equity_baseline_usdt,
        open_position_count=0,
        open_position_sides={},
        total_initial_margin_used_usdt=Decimal(0),
        daily_realized_loss_pct=daily_realized_loss_pct,
    )


def get_or_create_risk_config_snapshot(
    session: Session, config: RiskConfig, *, clock: datetime
) -> RiskConfigSnapshot:
    """Reuses the latest snapshot if its values are unchanged; else appends a new one.

    `config_snapshot_id` is derived from the values themselves, so a rerun
    with identical settings naturally resolves to the same row
    (`on_conflict_do_nothing`), never a duplicate.
    """
    values = (
        config.leverage,
        config.default_stop_roe_pct,
        config.position_size_pct,
        config.max_concurrent_positions,
        config.daily_loss_kill_switch_pct,
        config.max_receive_lag_seconds,
        config.max_price_deviation_bps,
        config.equity_baseline_usdt,
    )
    snapshot_id = _config_snapshot_id(values)
    existing = session.get(RiskConfigSnapshot, snapshot_id)
    if existing is not None:
        return existing
    session.execute(
        pg_insert(RiskConfigSnapshot)
        .values(
            config_snapshot_id=snapshot_id,
            leverage=config.leverage,
            default_stop_roe_pct=config.default_stop_roe_pct,
            position_size_pct=config.position_size_pct,
            max_concurrent_positions=config.max_concurrent_positions,
            daily_loss_kill_switch_pct=config.daily_loss_kill_switch_pct,
            max_receive_lag_seconds=config.max_receive_lag_seconds,
            max_price_deviation_bps=config.max_price_deviation_bps,
            equity_baseline_usdt=config.equity_baseline_usdt,
            effective_at=clock,
        )
        .on_conflict_do_nothing(index_elements=["config_snapshot_id"])
    )
    session.flush()
    row = session.get(RiskConfigSnapshot, snapshot_id)
    assert row is not None
    return row


def load_unprocessed_approved_events(
    session: Session,
) -> list[tuple[SignalDecisionEvent, NormalizedSignal, TelegramMessageVersion]]:
    """Every `APPROVED` decision that has not yet produced a `TradeIntent`.

    Joins all the way back to `telegram_message_versions` for the real
    `source_date`/`received_at` (BR-006 freshness) -- `NormalizedSignal.created_at`
    is when the row was *parsed*, not when the author actually posted it,
    the same distinction that mattered for the Control Bot's `expires_at`
    fix (see docs/phase-5/known-issues.md P5-MAJOR-002).
    """
    already_processed = select(TradeIntentRow.origin_event_id)
    stmt = (
        select(SignalDecisionEvent, NormalizedSignal, TelegramMessageVersion)
        .join(
            SignalDecisionRequest,
            SignalDecisionEvent.request_id == SignalDecisionRequest.request_id,
        )
        .join(
            NormalizedSignal,
            SignalDecisionRequest.signal_row_id == NormalizedSignal.signal_row_id,
        )
        .join(
            TelegramMessageVersion,
            NormalizedSignal.raw_message_id == TelegramMessageVersion.source_event_id,
        )
        .where(
            SignalDecisionEvent.outcome == "APPROVED",
            SignalDecisionEvent.event_id.not_in(already_processed),
        )
        .order_by(SignalDecisionEvent.decided_at)
    )
    return list(session.execute(stmt).all())  # type: ignore[arg-type]


def effective_entry(
    signal: NormalizedSignal,
    *,
    entry_type: str | None,
    entry_value: Decimal | None,
) -> tuple[str, list[Decimal]]:
    """The entry the user actually approved: their override if any, else the signal's own.

    A `MARKET` override carries no price; a `LIMIT` override carries exactly one.
    """
    if entry_type == "MARKET":
        return "MARKET", []
    if entry_type == "LIMIT" and entry_value is not None:
        return "LIMIT", [entry_value]
    assert signal.entry_type is not None
    return signal.entry_type, [Decimal(str(v)) for v in (signal.entry_values or [])]


def _signal_timestamps(
    signal: NormalizedSignal, raw_message: TelegramMessageVersion
) -> tuple[datetime, datetime]:
    """`(source_date, received_at)` for the Risk Engine's receive-lag check (BR-006).

    That check asks "is the collector keeping up with the channel", which only matters for a
    signal that is acted on right as it is posted. A draft raised by the analysis-setup monitor is
    built hours after the analyst's post, from market data, so the original message's ingest lag
    (e.g. a backfilled message) says nothing about it: user decision 2026-10-04, such drafts skip
    this check. The user still approves each one, and its entry/stop/size are still validated.
    """
    if signal.parser_version.startswith(SETUP_PARSER_VERSION_PREFIX):
        return raw_message.source_date, raw_message.source_date
    return raw_message.source_date, raw_message.received_at


def _config_with_size(config: RiskConfig, position_size_pct: Decimal | None) -> RiskConfig:
    """Per-trade size override (fraction of equity as margin); `None` keeps the default."""
    if position_size_pct is None:
        return config
    return replace(config, position_size_pct=position_size_pct)


def _create_trade_intent(
    session: Session,
    event: SignalDecisionEvent,
    signal: NormalizedSignal,
    *,
    status: str,
) -> TradeIntentRow:
    """`status` must already be the fully-resolved outcome (`RISK_APPROVED`/

    `RISK_REJECTED`), decided by the caller *before* this insert -- the row
    is append-only (no UPDATE trigger permits it), so there is no later
    transition from a transient `CREATED` state the way the logical state
    machine describes; evaluation and creation happen atomically in the
    same transaction instead.
    """
    intent_id = _intent_id_for_event(event.event_id)
    take_profits = event.approved_take_profits if event.approved_take_profits is not None else []
    entry_type, entry_values = (
        effective_entry(
            signal,
            entry_type=event.approved_entry_type,
            entry_value=event.approved_entry_value,
        )
        if signal.entry_type is not None
        else (None, [])
    )
    session.execute(
        pg_insert(TradeIntentRow)
        .values(
            intent_id=intent_id,
            origin_event_id=event.event_id,
            signal_row_id=signal.signal_row_id,
            channel_id=signal.channel_id,
            symbol=signal.symbol,
            side=signal.side,
            entry_type=entry_type,
            entry_values=[str(value) for value in entry_values],
            stop_value=event.approved_stop_value,
            take_profits=take_profits,
            status=status,
        )
        .on_conflict_do_nothing(index_elements=["intent_id"])
    )
    session.flush()
    row = session.get(TradeIntentRow, intent_id)
    assert row is not None
    return row


def _notify_risk_rejected(
    session: Session, intent: TradeIntentRow, evaluation: RiskEvaluation
) -> None:
    """Semi-automatic bot (2026-09-29, explicit user request): an approved signal the
    Risk Engine refuses must reach the user, not just the database. Uses the
    `execution_` prefix so Control Bot's existing execution-event loop forwards it.
    """
    append_outbox_event(
        session,
        event_id=hashlib.sha256(
            f"outbox:{intent.intent_id}:execution_risk_rejected".encode()
        ).hexdigest(),
        event_type="execution_risk_rejected",
        aggregate_type="trade_intent",
        aggregate_id=intent.intent_id,
        payload={
            "symbol": intent.symbol,
            "side": intent.side,
            "reason_codes": ", ".join(evaluation.reason_codes) or "-",
        },
    )


def _record_risk_decision(
    session: Session,
    intent: TradeIntentRow,
    evaluation: RiskEvaluation,
    *,
    market_quote: MarketPriceQuote,
    account: AccountState,
    config_snapshot_id: str,
    now: datetime,
    expiry_seconds: float,
) -> None:
    decision_id = _decision_id_for_intent(intent.intent_id)
    session.execute(
        pg_insert(RiskDecisionRow)
        .values(
            decision_id=decision_id,
            intent_id=intent.intent_id,
            verdict=evaluation.verdict,
            reason_codes=evaluation.reason_codes,
            computed_stop_price=evaluation.computed_stop_price,
            quantity=evaluation.quantity,
            entry_price_used=evaluation.entry_price_used,
            market_price=market_quote.price,
            market_price_fetched_at=market_quote.fetched_at,
            equity_used=account.equity_usdt,
            open_position_count_used=account.open_position_count,
            daily_realized_loss_pct_used=account.daily_realized_loss_pct,
            config_snapshot_id=config_snapshot_id,
            expires_at=now + timedelta(seconds=expiry_seconds),
        )
        .on_conflict_do_nothing(index_elements=["decision_id"])
    )


def _apply_approved_intent_to_account(
    account: AccountState,
    intent: TradeIntentRow,
    evaluation: RiskEvaluation,
    *,
    leverage: int,
) -> AccountState:
    """In-batch bookkeeping only -- see module docstring's Slice-1 caveat."""
    if (
        evaluation.verdict != "APPROVED"
        or evaluation.quantity is None
        or evaluation.entry_price_used is None
    ):
        return account
    margin_used = (evaluation.quantity * evaluation.entry_price_used) / Decimal(leverage)
    return replace(
        account,
        open_position_count=account.open_position_count + 1,
        open_position_sides={**account.open_position_sides, intent.symbol: intent.side},
        total_initial_margin_used_usdt=account.total_initial_margin_used_usdt + margin_used,
    )


def preview_trade_intent(
    signal: NormalizedSignal,
    raw_message: TelegramMessageVersion,
    *,
    config: RiskConfig,
    market_data_client: Any,
    now: datetime,
    daily_realized_loss_pct: Decimal = Decimal(0),
    draft: DraftValues | None = None,
) -> RiskEvaluation:
    """Read-only preview for the Control Bot's pre-approval notification.

    `draft` carries the user's pending edits (entry, size, stop); without it the signal's own
    values are previewed.

    Computes exactly what `run_risk_evaluation` would compute for this
    signal if it were approved right now, using a clean-slate `AccountState`
    (same Slice-1 limitation as `run_risk_evaluation` itself -- see this
    module's docstring). Writes nothing to the database: this is shown to
    the user *before* they decide, not a recorded `RiskDecision`. The
    authoritative evaluation still runs post-approval via
    `run_risk_evaluation`, against whatever the market/account looks like
    at that later moment -- this preview can and will go stale between
    notification and approval, same as the signal's own price-deviation
    check already tolerates.
    """
    if (
        signal.symbol is None
        or signal.side is None
        or signal.entry_type is None
        or (signal.status != "VALIDATED")
    ):
        return RiskEvaluation(verdict="REJECTED", reason_codes=[REASON_SIGNAL_NOT_VALIDATED])

    try:
        market_price: Decimal | None = fetch_mark_price(
            market_data_client, signal.symbol, clock=lambda: now
        ).price
    except Exception:
        LOGGER.warning(
            "preview could not fetch mark price; showing no quantity preview",
            extra={"context": {"symbol": signal.symbol, "signal_row_id": signal.signal_row_id}},
        )
        market_price = None

    preview_entry_type, preview_entry_values = effective_entry(
        signal,
        entry_type=draft.entry_type if draft is not None else None,
        entry_value=draft.entry_value if draft is not None else None,
    )
    reference_price: Decimal | None = None
    if preview_entry_type == "MARKET" and market_price is not None:
        try:
            reference_price = fetch_reference_price(
                market_data_client, signal.symbol, at=raw_message.source_date
            )
        except Exception:
            LOGGER.warning(
                "preview could not fetch reference price; BR-006 deviation check skipped",
                extra={"context": {"symbol": signal.symbol, "signal_row_id": signal.signal_row_id}},
            )

    source_date, received_at = _signal_timestamps(signal, raw_message)
    intent_input = TradeIntentInput(
        symbol=signal.symbol,
        side=signal.side,
        entry_type=preview_entry_type,
        entry_values=preview_entry_values,
        stop_value=draft.stop_value if draft is not None else signal.stop_value,
        signal_source_date=source_date,
        signal_received_at=received_at,
    )
    evaluation = evaluate_trade_intent(
        intent_input,
        config=_config_with_size(config, draft.position_size_pct if draft is not None else None),
        account=default_starting_account_state(
            config, daily_realized_loss_pct=daily_realized_loss_pct
        ),
        market_price=market_price,
        reference_price=reference_price,
        now=now,
    )
    # A limit price far from the market is most often a typo: surface it, never block on it.
    if (
        preview_entry_type == "LIMIT"
        and preview_entry_values
        and market_price is not None
        and market_price > 0
        and abs(preview_entry_values[0] - market_price) / market_price > _ENTRY_FAR_FROM_MARKET
    ):
        evaluation = replace(
            evaluation,
            advisory_codes=[*evaluation.advisory_codes, ADVISORY_ENTRY_FAR_FROM_MARKET],
        )
    return evaluation


def run_risk_evaluation(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    market_data_client: Any,
    clock: Any = lambda: datetime.now(UTC),
) -> list[RiskPipelineResult]:
    """Evaluate every unprocessed `APPROVED` signal decision into a `TradeIntent`+`RiskDecision`."""
    config = risk_config_from_settings(settings)
    now = clock()
    results: list[RiskPipelineResult] = []

    with session_factory.begin() as session:
        account = default_starting_account_state(
            config,
            daily_realized_loss_pct=daily_loss_pct(session, now, config.equity_baseline_usdt),
        )
        pause = active_pause(session, now)
        config_row = get_or_create_risk_config_snapshot(session, config, clock=now)
        pending = load_unprocessed_approved_events(session)
        for event, signal, raw_message in pending:
            intent_id = _intent_id_for_event(event.event_id)
            if signal.symbol is None or signal.side is None or signal.entry_type is None:
                # Defensive, should not happen: even a NEW (unresolved-symbol) signal
                # already has symbol/side populated by the parser (see the
                # SIGNAL_NOT_VALIDATED case below) -- this guards against a
                # genuinely null field, not against an unresolved one.
                LOGGER.error(
                    "approved signal decision missing symbol/side/entry_type; skipping",
                    extra={"context": {"event_id": event.event_id, "intent_id": intent_id}},
                )
                continue
            if pause is not None or signal.status != "VALIDATED":
                # Real gap found running this against the real database: the Control
                # Bot notifies (and the user may approve) both NEW and VALIDATED
                # signals (signal_decisions.load_pending_signals), but only VALIDATED
                # means the symbol actually resolved against real market data. A NEW
                # signal's `symbol` can be bare, unresolved candidate text (e.g. "LSK"
                # instead of "LSKUSDT") -- not a real ticker, and Binance's own API
                # would 400 on it. Reject explicitly rather than let a confusing raw
                # HTTP error stand in for the real reason.
                # The user answered STOP to today's daily-loss alert: no new orders
                # until it lapses at the next UTC midnight.
                evaluation = RiskEvaluation(
                    verdict="REJECTED",
                    reason_codes=[
                        REASON_TRADING_PAUSED if pause is not None else REASON_SIGNAL_NOT_VALIDATED
                    ],
                    computed_stop_price=None,
                    quantity=None,
                    entry_price_used=None,
                )
                intent = _create_trade_intent(session, event, signal, status="RISK_REJECTED")
                _record_risk_decision(
                    session,
                    intent,
                    evaluation,
                    market_quote=MarketPriceQuote(
                        symbol=signal.symbol, price=Decimal(0), fetched_at=now
                    ),
                    account=account,
                    config_snapshot_id=config_row.config_snapshot_id,
                    now=now,
                    expiry_seconds=settings.risk_decision_expiry_seconds,
                )
                _notify_risk_rejected(session, intent, evaluation)
                results.append(
                    RiskPipelineResult(
                        intent_id=intent.intent_id,
                        symbol=signal.symbol,
                        verdict=evaluation.verdict,
                        reason_codes=evaluation.reason_codes,
                    )
                )
                continue
            try:
                market_quote = fetch_mark_price(market_data_client, signal.symbol, clock=clock)
                market_price: Decimal | None = market_quote.price
            except Exception:
                LOGGER.exception(
                    "risk engine could not fetch mark price; failing closed",
                    extra={"context": {"symbol": signal.symbol, "intent_id": intent_id}},
                )
                market_quote = MarketPriceQuote(
                    symbol=signal.symbol, price=Decimal(0), fetched_at=now
                )
                market_price = None

            approved_entry_type, entry_values = effective_entry(
                signal,
                entry_type=event.approved_entry_type,
                entry_value=event.approved_entry_value,
            )
            reference_price: Decimal | None = None
            if approved_entry_type == "MARKET":
                try:
                    reference_price = fetch_reference_price(
                        market_data_client, signal.symbol, at=raw_message.source_date
                    )
                except Exception:
                    LOGGER.exception(
                        "risk engine could not fetch reference price; failing closed",
                        extra={"context": {"symbol": signal.symbol, "intent_id": intent_id}},
                    )

            stop_value = event.approved_stop_value
            source_date, received_at = _signal_timestamps(signal, raw_message)
            intent_input = TradeIntentInput(
                symbol=signal.symbol,
                side=signal.side,
                entry_type=approved_entry_type,
                entry_values=entry_values,
                stop_value=stop_value,
                signal_source_date=source_date,
                signal_received_at=received_at,
            )
            evaluation = evaluate_trade_intent(
                intent_input,
                config=_config_with_size(config, event.approved_position_size_pct),
                account=account,
                market_price=market_price,
                reference_price=reference_price,
                now=now,
            )
            resolved_status = (
                "RISK_APPROVED" if evaluation.verdict == "APPROVED" else "RISK_REJECTED"
            )
            intent = _create_trade_intent(session, event, signal, status=resolved_status)
            _record_risk_decision(
                session,
                intent,
                evaluation,
                market_quote=market_quote,
                account=account,
                config_snapshot_id=config_row.config_snapshot_id,
                now=now,
                expiry_seconds=settings.risk_decision_expiry_seconds,
            )
            if resolved_status == "RISK_REJECTED":
                _notify_risk_rejected(session, intent, evaluation)
            account = _apply_approved_intent_to_account(
                account, intent, evaluation, leverage=config.leverage
            )
            results.append(
                RiskPipelineResult(
                    intent_id=intent.intent_id,
                    symbol=signal.symbol,
                    verdict=evaluation.verdict,
                    reason_codes=evaluation.reason_codes,
                )
            )
    return results
