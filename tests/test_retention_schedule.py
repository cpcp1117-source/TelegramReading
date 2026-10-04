from __future__ import annotations

import asyncio
from typing import Any

import pytest

from telegram_trader.retention_cleanup import RetentionCleanupResult, run_retention_forever


def test_retention_runs_after_first_delay_then_each_interval_and_survives_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 3:
            raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    runs: list[Any] = []

    def cleanup(session_factory: Any, media_store: Any) -> list[RetentionCleanupResult]:
        runs.append((session_factory, media_store))
        if len(runs) == 2:
            raise RuntimeError("database unavailable")
        return [RetentionCleanupResult(1, 0, 3, 1, 0)]

    async def scenario() -> None:
        await run_retention_forever(
            "factory",  # type: ignore[arg-type]
            "media",  # type: ignore[arg-type]
            interval_seconds=86400,
            first_delay_seconds=60,
            cleanup=cleanup,
            migration_applied=lambda _factory: True,
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    assert sleeps == [60, 86400, 86400, 86400]
    assert runs == [("factory", "media")] * 3


def test_retention_is_skipped_until_the_60_day_migration_is_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    runs: list[Any] = []

    def cleanup(*args: Any) -> list[RetentionCleanupResult]:
        runs.append(args)
        return []

    async def scenario() -> None:
        await run_retention_forever(
            "factory",  # type: ignore[arg-type]
            "media",  # type: ignore[arg-type]
            cleanup=cleanup,
            migration_applied=lambda _factory: False,
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    assert runs == []
