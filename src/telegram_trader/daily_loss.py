"""Daily loss kill switch, as a question to the user rather than an automatic block.

User decision 2026-10-04: the trading day starts at UTC 00:00 (Taiwan 08:00).
When the day's realized net loss reaches `RISK_DAILY_LOSS_KILL_SWITCH_PCT` of
the equity baseline, one alert per day goes to Telegram with STOP / CONTINUE
buttons. STOP pauses new orders until the next UTC midnight; nothing is closed
and positions already open keep their stop-loss/take-profit orders.

The loss is computed from `realized_pnl` (net of the closing fee), so it only
counts closes this system recorded -- a manual trade on Binance is not seen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from telegram_trader.audit import append_audit_event
from telegram_trader.models import OutboxEvent, RealizedPnl, TradingPauseDecision
from telegram_trader.outbox import append_outbox_event

DAILY_LOSS_EVENT_TYPE = "execution_daily_loss_limit_reached"
PAUSE_DECIDED_EVENT_TYPE = "trading_pause_decided"
AGGREGATE_TYPE = "trading_control"


@dataclass(frozen=True, slots=True)
class DailyLossLimit:
    equity_usdt: Decimal | None
    threshold_pct: Decimal


def utc_day_start(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def daily_net_pnl(session: Session, now: datetime) -> Decimal:
    """Today's (UTC day) realized net PnL; closes whose fills were unavailable count as 0."""
    total = session.scalar(
        select(
            func.sum(RealizedPnl.realized_pnl - func.coalesce(RealizedPnl.exit_commission, 0))
        ).where(RealizedPnl.closed_at >= utc_day_start(now), RealizedPnl.closed_at <= now)
    )
    return Decimal(total) if total is not None else Decimal(0)


def daily_loss_pct(session: Session, now: datetime, equity_usdt: Decimal | None) -> Decimal:
    """Today's net PnL as a fraction of equity, negative for a loss (`-0.06` = -6%)."""
    if not equity_usdt:
        return Decimal(0)
    return daily_net_pnl(session, now) / equity_usdt


def alert_event_id(day_start: datetime) -> str:
    return hashlib.sha256(f"daily_loss_limit:{day_start.isoformat()}".encode()).hexdigest()


def alert_if_daily_loss_limit_reached(
    session: Session, limit: DailyLossLimit, now: datetime
) -> bool:
    """Queue the day's single kill-switch alert once the loss reaches the limit.

    Idempotent per UTC day: the event id is derived from the day, so a second
    losing close the same day does not ask again. Returns True if queued now.
    """
    if not limit.equity_usdt:
        return False
    net = daily_net_pnl(session, now)
    loss_pct = net / limit.equity_usdt
    if loss_pct > -limit.threshold_pct:
        return False
    day_start = utc_day_start(now)
    event_id = alert_event_id(day_start)
    if session.get(OutboxEvent, event_id) is not None:
        return False
    payload = {
        "day_start_utc": day_start.isoformat(),
        "net_pnl": _plain(net),
        "loss_pct": _plain((-loss_pct * 100).quantize(Decimal("0.01"))),
        "threshold_pct": _plain((limit.threshold_pct * 100).quantize(Decimal("0.01"))),
        "equity_usdt": _plain(limit.equity_usdt),
    }
    for append in (append_audit_event, append_outbox_event):
        append(
            session,
            event_id=event_id,
            event_type=DAILY_LOSS_EVENT_TYPE,
            aggregate_type=AGGREGATE_TYPE,
            aggregate_id=day_start.date().isoformat(),
            payload=payload,
        )
    return True


def active_pause(session: Session, now: datetime) -> TradingPauseDecision | None:
    return session.scalar(
        select(TradingPauseDecision)
        .where(TradingPauseDecision.decision == "STOP", TradingPauseDecision.paused_until > now)
        .order_by(TradingPauseDecision.paused_until.desc())
        .limit(1)
    )


@dataclass(frozen=True, slots=True)
class PauseDecisionResult:
    decision: str
    paused_until: datetime | None
    duplicate: bool


def record_pause_decision(
    session: Session,
    alert: OutboxEvent,
    *,
    decision: str,
    actor_user_id: int,
    now: datetime,
) -> PauseDecisionResult:
    """Record the first answer to `alert`; a later tap returns that first answer unchanged.

    A STOP pauses until the end of the alert's own UTC day -- answering late,
    after midnight, cannot pause the next day by accident.
    """
    if decision not in ("STOP", "CONTINUE"):
        raise ValueError(f"unknown pause decision {decision!r}")
    existing = session.scalar(
        select(TradingPauseDecision).where(TradingPauseDecision.source_event_id == alert.event_id)
    )
    if existing is not None:
        return PauseDecisionResult(existing.decision, existing.paused_until, duplicate=True)
    paused_until = None
    if decision == "STOP":
        alert_day = datetime.fromisoformat(str(alert.payload["day_start_utc"]))
        paused_until = alert_day + timedelta(days=1)
    decision_id = hashlib.sha256(f"pause:{alert.event_id}".encode()).hexdigest()
    session.add(
        TradingPauseDecision(
            decision_id=decision_id,
            source_event_id=alert.event_id,
            decision=decision,
            paused_until=paused_until,
            actor_user_id=actor_user_id,
            decided_at=now,
        )
    )
    append_audit_event(
        session,
        event_id=decision_id,
        event_type=PAUSE_DECIDED_EVENT_TYPE,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=alert.aggregate_id,
        payload={
            "decision": decision,
            "paused_until": paused_until.isoformat() if paused_until else None,
            "actor_user_id": actor_user_id,
        },
    )
    return PauseDecisionResult(decision, paused_until, duplicate=False)


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
