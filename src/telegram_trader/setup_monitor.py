"""Watch active `analysis_setups` against Binance candles and raise a draft when confirmed.

No AI here. Each run replays the closed candles since the setup's `valid_from` through the pure
`analysis_setup.evaluate_candles`, then records what changed:

- first touch of the entry zone  -> `setup_zone_touched` notification (stage 1, "heads up")
- confirmation candle closes     -> `setup_triggered` notification + a `normalized_signals` row
                                    (stage 2): the existing Control Bot approval -> Risk Engine ->
                                    Execution Gateway path takes over, and the user still decides.
- stop / target-first / expiry   -> `setup_invalidated` / `setup_expired` notice, setup closed.

Replaying from scratch each run makes the monitor stateless and idempotent: the stored status is
only used to decide which notifications are new.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.analysis_setup import (
    Candle,
    PriceZone,
    SetupState,
    SetupTransition,
    evaluate_candles,
)
from telegram_trader.models import AnalysisSetup, NormalizedContent, NormalizedSignal
from telegram_trader.outbox import append_outbox_event
from telegram_trader.setup_extraction import setup_event_id

LOGGER = logging.getLogger(__name__)

SETUP_PARSER_VERSION_PREFIX = "setup-"
_SIGNAL_VALIDITY = timedelta(hours=6)
_MAX_TRIGGER_AGE = timedelta(hours=2)
_ACTIVE_STATUSES = ("WATCHING", "ZONE_TOUCHED")
_INTERVAL_SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


@dataclass(frozen=True, slots=True)
class SetupMonitorResult:
    checked_count: int
    touched_count: int
    triggered_count: int
    closed_count: int
    error_count: int


def parse_candles(rows: list[list[Any]]) -> list[Candle]:
    """Binance kline rows -> `Candle`s (open_time/close_time are epoch milliseconds)."""
    return [
        Candle(
            open_time=datetime.fromtimestamp(int(row[0]) / 1000, UTC),
            close_time=datetime.fromtimestamp((int(row[6]) + 1) / 1000, UTC),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
        )
        for row in rows
    ]


def _state_of(setup: AnalysisSetup) -> SetupState:
    # Replay always starts from WATCHING: the rules are deterministic, so the replay re-derives
    # any earlier touch; the stored status only decides which notifications are new.
    targets = [PriceZone(Decimal(tp["low"]), Decimal(tp["high"])) for tp in setup.take_profits]
    return SetupState(
        side=setup.side,  # type: ignore[arg-type]
        status="WATCHING",
        entry_zone=PriceZone(setup.entry_zone_low, setup.entry_zone_high),
        confirm_rule=setup.confirm_rule,  # type: ignore[arg-type]
        trigger_price=setup.trigger_price,
        stop_value=setup.stop_value,
        first_target=targets[0] if targets else None,
        cancel_if_target_first=setup.cancel_if_target_first,
        valid_from=setup.valid_from,
        expires_at=setup.expires_at,
    )


def _take_profit_values(setup: AnalysisSetup) -> list[str]:
    """The edge of each target zone nearest to entry -- the conservative fill assumption."""
    values = []
    for tp in setup.take_profits:
        values.append(tp["high"] if setup.side == "SHORT" else tp["low"])
    return values


def _create_signal(
    session: Session, setup: AnalysisSetup, transition: SetupTransition, now: datetime
) -> str:
    """Insert the `VALIDATED` signal the existing approval pipeline consumes; return its id."""
    assert transition.entry_price is not None
    signal_id = hashlib.sha256(f"setup:{setup.setup_id}".encode()).hexdigest()
    parser_version = f"{SETUP_PARSER_VERSION_PREFIX}{setup.setup_id[:12]}"
    normalizer_version = session.scalar(
        select(NormalizedContent.normalizer_version).where(
            NormalizedContent.content_id == setup.content_id
        )
    )
    session.add(
        NormalizedSignal(
            signal_row_id=hashlib.sha256(
                f"{setup.raw_message_id}:{parser_version}".encode()
            ).hexdigest(),
            signal_id=signal_id,
            revision=0,
            raw_message_id=setup.raw_message_id,
            normalizer_version=normalizer_version or "unknown",
            channel_id=setup.channel_id,
            topic_id=setup.topic_id,
            status="VALIDATED",
            symbol=setup.symbol,
            side=setup.side,
            entry_type="LIMIT",
            entry_values=[str(transition.entry_price)],
            stop_value=setup.stop_value,
            stop_origin="AUTHOR" if setup.stop_value is not None else "DEFAULT_ROE_30",
            take_profits=_take_profit_values(setup),
            evidence_spans={},
            link_method=None,
            parser_version=parser_version,
            expires_at=now + _SIGNAL_VALIDITY,
        )
    )
    return signal_id


def _payload(setup: AnalysisSetup, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "setup_id": setup.setup_id[:12],
        "symbol": setup.symbol,
        "side": setup.side,
        "entry_zone": f"{setup.entry_zone_low.normalize():f}-{setup.entry_zone_high.normalize():f}",
        "stop": f"{setup.stop_value.normalize():f}" if setup.stop_value is not None else None,
    }
    payload.update(extra)
    return payload


def _emit(session: Session, setup: AnalysisSetup, event_type: str, payload: dict[str, Any]) -> None:
    append_outbox_event(
        session,
        event_id=setup_event_id(setup.setup_id, event_type),
        event_type=event_type,
        aggregate_type="analysis_setup",
        aggregate_id=setup.setup_id,
        payload=payload,
    )


def apply_transition(
    session: Session, setup: AnalysisSetup, transition: SetupTransition, now: datetime
) -> tuple[bool, bool, bool]:
    """Persist `transition` onto `setup`; returns `(touched, triggered, closed)` flags."""
    touched = triggered = closed = False
    stored = setup.status
    setup.last_checked_at = now

    if "ZONE_TOUCHED" in transition.events and stored == "WATCHING":
        touched = True
        setup.touched_at = transition.touched_at
        _emit(
            session,
            setup,
            "setup_zone_touched",
            _payload(setup, note="價格已碰到進場區，等待 K 線收盤確認；確認後才會送出開單草稿"),
        )

    if transition.status == "TRIGGERED":
        triggered = True
        setup.status = "TRIGGERED"
        setup.triggered_at = transition.triggered_at
        setup.touched_at = setup.touched_at or transition.touched_at
        setup.signal_id = _create_signal(session, setup, transition, now)
        _emit(
            session,
            setup,
            "setup_triggered",
            _payload(
                setup,
                entry_price=f"{transition.entry_price.normalize():f}"
                if transition.entry_price is not None
                else None,
                note="條件確認，開單草稿已建立；請在下一則訊息按核准或拒絕",
            ),
        )
    elif transition.status in ("INVALIDATED", "EXPIRED"):
        closed = True
        setup.status = transition.status
        setup.status_reason = transition.reason
        _emit(
            session,
            setup,
            "setup_invalidated" if transition.status == "INVALIDATED" else "setup_expired",
            _payload(setup, reason=transition.reason),
        )
    elif transition.status == "ZONE_TOUCHED":
        setup.status = "ZONE_TOUCHED"
    return touched, triggered, closed


def run_setup_monitor(
    session_factory: sessionmaker[Session],
    market_client: Any,
    *,
    interval: str = "15m",
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SetupMonitorResult:
    interval_seconds = _INTERVAL_SECONDS[interval]
    with session_factory() as session:
        setup_ids = list(
            session.scalars(
                select(AnalysisSetup.setup_id).where(AnalysisSetup.status.in_(_ACTIVE_STATUSES))
            )
        )

    checked = touched_count = triggered_count = closed_count = errors = 0
    for setup_id in setup_ids:
        now = clock()
        try:
            with session_factory.begin() as session:
                setup = session.get(AnalysisSetup, setup_id, with_for_update=True)
                if setup is None or setup.status not in _ACTIVE_STATUSES:
                    continue
                rows = market_client.get_candles(
                    setup.symbol,
                    interval=interval,
                    start_time_ms=int(setup.valid_from.timestamp() * 1000)
                    - interval_seconds * 1000,
                    limit=1000,
                )
                transition = evaluate_candles(
                    _state_of(setup), parse_candles(rows), now=now, max_trigger_age=_MAX_TRIGGER_AGE
                )
                touched, triggered, closed = apply_transition(session, setup, transition, now)
                checked += 1
                touched_count += touched
                triggered_count += triggered
                closed_count += closed
        except Exception:
            # One symbol's market-data failure must not stop the other setups; it is retried on
            # the next run, and nothing has been written for this setup (transaction rolled back).
            LOGGER.exception(
                "setup monitor failed for one setup; will retry",
                extra={"context": {"setup_id": setup_id}},
            )
            errors += 1
    return SetupMonitorResult(checked, touched_count, triggered_count, closed_count, errors)
