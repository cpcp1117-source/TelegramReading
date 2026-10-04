"""Analysis-setup worker: normalize analyst messages, let the AI describe them, watch the market.

One long-running loop, intended to run **on the host** next to the logged-in Codex CLI (the
containers have no AI login). Each cycle runs, in order:

1. `refresh_snapshot` when the Binance symbol snapshot is stale (symbol validation needs it)
2. `run_normalization`   -- new raw messages -> `normalized_content`
3. `run_setup_extraction` -- AI turns analyst messages into stored setups (slow: ~10-30 s each)
4. `run_setup_monitor`    -- cheap, no AI: replay market candles, notify, raise drafts

Steps 3 and 4 are separate cadences: monitoring must not wait behind a slow AI call, so the
extraction runs at most every `--extract-seconds`, the monitor every cycle. A failing step is
notified once and its recovery once (same `StepHealth` idea as the execution worker).
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.ai_client import create_ai_client
from telegram_trader.binance_market_data import (
    BinanceMarketDataClient,
    load_latest_snapshot,
    refresh_snapshot,
)
from telegram_trader.config import Settings, get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.normalize_content import run_normalization
from telegram_trader.outbox import append_outbox_event
from telegram_trader.setup_extraction import run_setup_extraction, setup_event_id
from telegram_trader.setup_monitor import run_setup_monitor

LOGGER = logging.getLogger(__name__)

_DEFAULT_CYCLE_SECONDS = 30.0
_DEFAULT_EXTRACT_SECONDS = 60.0
_STEP_LABELS = {
    "snapshot": "幣種清單更新",
    "normalization": "訊息整理",
    "extraction": "AI 分析",
    "monitor": "市場監控",
}


class StepHealth:
    """Remembers which steps are failing so each outage is notified once, recovery once."""

    def __init__(self) -> None:
        self._failing: set[str] = set()

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
            self._failing.add(step)
            _notify(session_factory, step, "setup_worker_error", repr(error), now=now)
        elif error is None and was_failing:
            self._failing.discard(step)
            _notify(session_factory, step, "setup_worker_recovered", "", now=now)


def _notify(
    session_factory: sessionmaker[Session], step: str, event_type: str, error: str, *, now: datetime
) -> None:
    with session_factory.begin() as session:
        append_outbox_event(
            session,
            event_id=setup_event_id(step, event_type, now.isoformat()),
            event_type=event_type,
            aggregate_type="setup_worker",
            aggregate_id=step,
            payload={"step": _STEP_LABELS[step], "error": error[:300] or None},
        )


def run_cycle(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    market_data_client: Any,
    ai_client: Any,
    health: StepHealth,
    run_extraction: bool,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    def ensure_snapshot() -> None:
        with session_factory() as session:
            fresh = load_latest_snapshot(
                session,
                clock=clock,
                max_age_seconds=settings.binance_snapshot_max_age_seconds / 2,
            )
        if fresh is None:
            refresh_snapshot(market_data_client, session_factory, clock=clock)

    steps: list[tuple[str, Callable[[], object]]] = [
        ("snapshot", ensure_snapshot),
        (
            "normalization",
            lambda: run_normalization(
                session_factory,
                snapshot_max_age_seconds=settings.binance_snapshot_max_age_seconds,
            ),
        ),
    ]
    if run_extraction:
        steps.append(
            (
                "extraction",
                lambda: run_setup_extraction(
                    session_factory,
                    ai_client,
                    model=settings.setup_model,
                    batch_size=settings.setup_batch_size,
                    context_message_count=settings.setup_context_message_count,
                    expiry_hours=settings.setup_expiry_hours,
                    clock=clock,
                ),
            )
        )
    steps.append(
        (
            "monitor",
            lambda: run_setup_monitor(
                session_factory,
                market_data_client,
                interval=settings.setup_confirm_interval,
                clock=clock,
            ),
        )
    )
    for step, run in steps:
        try:
            run()
        except Exception as error:
            LOGGER.exception("setup worker step failed", extra={"context": {"step": step}})
            health.record(session_factory, step, error, now=clock())
        else:
            health.record(session_factory, step, None, now=clock())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Long-running analysis-setup worker (run on the host, next to the Codex CLI)."
    )
    parser.add_argument("--interval-seconds", type=float, default=_DEFAULT_CYCLE_SECONDS)
    parser.add_argument("--extract-seconds", type=float, default=_DEFAULT_EXTRACT_SECONDS)
    parser.add_argument("--once", action="store_true", help="run one full cycle and exit")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    market_data_client = BinanceMarketDataClient(
        httpx.Client(base_url=settings.binance_api_base_url, timeout=10.0)
    )
    ai_client = create_ai_client(settings)
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("stop requested; finishing the current cycle", extra={"context": {}})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    health = StepHealth()
    next_extraction = datetime.min.replace(tzinfo=UTC)
    LOGGER.info(
        "setup worker started",
        extra={
            "context": {
                "ai_provider": settings.ai_provider,
                "model": settings.setup_model,
                "interval_seconds": args.interval_seconds,
            }
        },
    )
    try:
        while not stop.is_set():
            started = datetime.now(UTC)
            run_extraction = started >= next_extraction
            run_cycle(
                session_factory,
                settings=settings,
                market_data_client=market_data_client,
                ai_client=ai_client,
                health=health,
                run_extraction=run_extraction,
            )
            if run_extraction:
                next_extraction = datetime.now(UTC) + timedelta(seconds=args.extract_seconds)
            if args.once:
                return 0
            stop.wait(args.interval_seconds)
        return 0
    finally:
        market_data_client.close()
        ai_client.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
