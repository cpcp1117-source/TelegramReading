"""Execution worker (Phase 6, 2026-09-29 explicit user request): approving a signal in
Control Bot is now all it takes -- this long-running service picks the approval up
within one cycle and carries it through Risk Engine, order placement and, later,
the position-closed notification, with no script to run by hand.

Each cycle runs three steps in order: `run_risk_evaluation` (APPROVED decision ->
TradeIntent + RiskDecision), `run_execution` (RISK_APPROVED intent -> entry,
stop-loss, take-profit), `run_position_monitor` (open position -> closed notice).

Why an always-on loop is now safe where the old one-shot script deliberately was
not: revision 0 of `order_lifecycle` is a per-intent claim, the entry is looked up
by `clientOrderId` before it is ever placed (P6-NOTICE-018), and an intent with any
lifecycle row is never picked up again -- so a restart can at worst leave one
intent half-done (notified, for the user to handle; P6-LIMIT-008), never submit
it twice. SIGTERM still lets the current cycle finish before exiting.

A step that starts failing is notified once, and its recovery once -- not every
5 seconds while it stays broken (e.g. no equity baseline configured, Binance down).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.binance_market_data import BinanceMarketDataClient
from telegram_trader.binance_trading_client import create_trading_client
from telegram_trader.config import Settings, get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.execution_gateway import run_execution
from telegram_trader.logging_config import configure_logging
from telegram_trader.outbox import append_outbox_event
from telegram_trader.position_monitor import run_position_monitor
from telegram_trader.trade_intent_pipeline import run_risk_evaluation

LOGGER = logging.getLogger(__name__)

_DEFAULT_CYCLE_SECONDS = 5.0
_STEP_LABELS = {
    "risk_evaluation": "風控計算",
    "execution": "下單",
    "position_monitor": "倉位監控",
}


class StepHealth:
    """Remembers which steps are currently failing, so each outage is notified once."""

    def __init__(self) -> None:
        self._failing: dict[str, str] = {}

    def record(
        self,
        session_factory: sessionmaker[Session],
        step: str,
        error: Exception | None,
        *,
        now: datetime,
    ) -> None:
        was_failing = step in self._failing
        if error is not None and not was_failing:
            self._failing[step] = repr(error)
            _notify_worker(session_factory, step, "execution_worker_error", repr(error), now=now)
        elif error is None and was_failing:
            previous = self._failing.pop(step)
            _notify_worker(session_factory, step, "execution_worker_recovered", previous, now=now)


def _notify_worker(
    session_factory: sessionmaker[Session],
    step: str,
    event_type: str,
    error: str,
    *,
    now: datetime,
) -> None:
    field = "error" if event_type == "execution_worker_error" else "previous_error"
    with session_factory.begin() as session:
        append_outbox_event(
            session,
            event_id=hashlib.sha256(
                f"outbox:execution_worker:{step}:{event_type}:{now.isoformat()}".encode()
            ).hexdigest(),
            event_type=event_type,
            aggregate_type="execution_worker",
            aggregate_id=step,
            payload={"step": _STEP_LABELS[step], field: error},
        )


def run_cycle(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    market_data_client: Any,
    trading_client: Any,
    health: StepHealth,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    steps: list[tuple[str, Callable[[], object]]] = [
        (
            "risk_evaluation",
            lambda: run_risk_evaluation(
                session_factory, settings=settings, market_data_client=market_data_client
            ),
        ),
        (
            "execution",
            lambda: run_execution(
                session_factory,
                settings=settings,
                market_data_client=market_data_client,
                trading_client=trading_client,
            ),
        ),
        (
            "position_monitor",
            lambda: run_position_monitor(session_factory, trading_client=trading_client),
        ),
    ]
    for step, run in steps:
        try:
            run()
        except Exception as error:
            LOGGER.exception("execution worker step failed", extra={"context": {"step": step}})
            health.record(session_factory, step, error, now=clock())
        else:
            health.record(session_factory, step, None, now=clock())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Long-running Execution Gateway worker: risk evaluation, order "
        "placement and position monitoring, one cycle every few seconds."
    )
    parser.add_argument("--interval-seconds", type=float, default=_DEFAULT_CYCLE_SECONDS)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    market_data_client = BinanceMarketDataClient(
        httpx.Client(base_url=settings.binance_api_base_url, timeout=10.0)
    )
    trading_client = create_trading_client(settings)
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("stop requested; finishing the current cycle", extra={"context": {}})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    health = StepHealth()
    LOGGER.info(
        "execution worker started",
        extra={"context": {"interval_seconds": args.interval_seconds}},
    )
    try:
        while not stop.is_set():
            run_cycle(
                session_factory,
                settings=settings,
                market_data_client=market_data_client,
                trading_client=trading_client,
                health=health,
            )
            stop.wait(args.interval_seconds)
        return 0
    finally:
        market_data_client.close()
        trading_client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
