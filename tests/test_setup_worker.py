from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from telegram_trader import setup_worker
from telegram_trader.config import Settings

NOW = datetime(2026, 10, 5, tzinfo=UTC)


class _FakeSessionFactory:
    @contextmanager
    def _session(self) -> Iterator[object]:
        yield object()

    def __call__(self) -> Any:
        return self._session()

    def begin(self) -> Any:
        return self._session()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    def recorder(name: str) -> Any:
        def run(*_: Any, **kwargs: Any) -> object:
            recorded.append(name)
            if name == "signal_parsing":
                recorded.append(f"size={kwargs['risk_flagged_size_pct']}")
            return None

        return run

    monkeypatch.setattr(setup_worker, "load_latest_snapshot", lambda *a, **k: object())
    monkeypatch.setattr(setup_worker, "run_normalization", recorder("normalization"))
    monkeypatch.setattr(setup_worker, "run_signal_parsing", recorder("signal_parsing"))
    monkeypatch.setattr(setup_worker, "run_setup_extraction", recorder("extraction"))
    monkeypatch.setattr(setup_worker, "run_setup_monitor", recorder("monitor"))
    return recorded


def _run(run_extraction: bool, health: setup_worker.StepHealth | None = None) -> None:
    setup_worker.run_cycle(
        _FakeSessionFactory(),  # type: ignore[arg-type]
        settings=Settings(),
        market_data_client=object(),
        ai_client=object(),
        health=health or setup_worker.StepHealth(),
        run_extraction=run_extraction,
        clock=lambda: NOW,
    )


def test_cycle_parses_signals_between_normalization_and_extraction(calls: list[str]) -> None:
    _run(run_extraction=True)

    assert [c for c in calls if not c.startswith("size=")] == [
        "normalization",
        "signal_parsing",
        "extraction",
        "monitor",
    ]


def test_signal_parsing_gets_the_risk_flagged_size(calls: list[str]) -> None:
    _run(run_extraction=False)

    assert f"size={Settings().risk_flagged_position_size_pct}" in calls
    assert Settings().risk_flagged_position_size_pct == Decimal("0.05")


def test_extraction_is_skipped_when_not_due_but_parsing_and_monitor_still_run(
    calls: list[str],
) -> None:
    _run(run_extraction=False)

    assert "extraction" not in calls
    assert {"signal_parsing", "monitor"} <= set(calls)


def test_a_failing_step_does_not_stop_the_later_steps(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("parse failed")

    monkeypatch.setattr(setup_worker, "run_signal_parsing", boom)
    notified: list[str] = []
    monkeypatch.setattr(
        setup_worker, "_notify", lambda _sf, step, event_type, *a, **k: notified.append(step)
    )

    _run(run_extraction=True)

    assert notified == ["signal_parsing"]
    assert "extraction" in calls and "monitor" in calls
