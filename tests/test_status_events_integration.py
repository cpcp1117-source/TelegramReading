"""Analyst status messages ("Tp1 !!", "先撤單") against a migrated PostgreSQL database.

A status message is an event about an idea already posted. It must close the setup it refers to
(never create or re-arm one), void a draft the user has not decided on, only notify when the user
already approved, and stay inert when it cannot tell which idea is meant.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

# Reuse the seeding helpers and the `engine` fixture of the setup-pipeline integration tests.
from test_setup_pipeline_integration import (
    CHANNEL,
    MODEL,
    T0,
    TOPIC,
    FakeAi,
    FakeMarket,
    _kline,
    _seed,
    _setup_json,
)
from test_setup_pipeline_integration import engine as _engine_fixture

from telegram_trader.db import create_session_factory
from telegram_trader.models import AnalysisSetup, NormalizedSignal, OutboxEvent
from telegram_trader.normalize_content import run_normalization
from telegram_trader.setup_extraction import run_setup_extraction
from telegram_trader.setup_monitor import run_setup_monitor
from telegram_trader.signal_decisions import (
    create_request,
    load_pending_signals,
    record_decision,
    request_id_for_signal_row,
)
from telegram_trader.telegram_storage import (
    MediaStore,
    TelegramMessageInput,
    TelegramMessageProcessor,
)

pytestmark = pytest.mark.integration

engine = _engine_fixture  # re-exported so pytest finds the shared fixture here
NOW = T0 + timedelta(hours=14)
UPDATE_AT = T0 + timedelta(hours=12)


def _post(
    factory: Any,
    tmp_path: Path,
    message_id: int,
    when: datetime,
    body: str,
    *,
    reply_to: int | None = None,
) -> None:
    TelegramMessageProcessor(
        factory, MediaStore(tmp_path / "media"), frozenset({(CHANNEL, TOPIC)})
    ).process(
        TelegramMessageInput(
            channel_id=CHANNEL,
            topic_id=TOPIC,
            message_id=message_id,
            event_kind="NEW",
            source_date=when,
            received_at=when,
            text=body,
            content_type="text",
            reply_to_message_id=reply_to,
        )
    )
    run_normalization(factory)


def _extract(factory: Any, ai: FakeAi) -> Any:
    return run_setup_extraction(factory, ai, model=MODEL, clock=lambda: NOW)[0]


def _watching_dot(factory: Any, tmp_path: Path) -> None:
    """The DOT SHORT setup from messages 1 (post) and 2 (update); message 2's setup is active."""
    _seed(factory, tmp_path, now=NOW)
    _extract(factory, FakeAi([_setup_json(stop_loss=1.23), _setup_json()]))


def _statuses(factory: Any) -> list[tuple[str, str, str | None]]:
    with factory() as session:
        rows = session.scalars(select(AnalysisSetup).order_by(AnalysisSetup.valid_from))
        return [(r.symbol, r.status, r.status_reason) for r in rows]


def _events(factory: Any) -> set[str]:
    with factory() as session:
        return set(session.scalars(select(OutboxEvent.event_type)))


def _trigger_dot(factory: Any) -> None:
    market = FakeMarket(
        [
            _kline(UPDATE_AT + timedelta(minutes=30), "1.17", "1.19", "1.17", "1.185"),
            _kline(UPDATE_AT + timedelta(minutes=45), "1.19", "1.195", "1.175", "1.1811"),
        ]
    )
    result = run_setup_monitor(factory, market, clock=lambda: NOW)
    assert result.triggered_count == 1


def test_target_hit_reply_closes_the_waiting_setup_without_an_ai_call(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    _watching_dot(factory, tmp_path)
    _post(factory, tmp_path, 3, T0 + timedelta(hours=13), "DOT\nTp1 !! 記得減倉套保", reply_to=1)

    result = _extract(factory, FakeAi([]))  # an AI call would fail: the response list is empty

    assert (result.processed_count, result.failed_count) == (1, 0)
    assert ("DOTUSDT", "INVALIDATED", "ANALYST_TARGET_HIT") in _statuses(factory)
    assert "setup_invalidated" in _events(factory)
    with factory() as session:
        assert len(list(session.scalars(select(AnalysisSetup)))) == 2  # nothing new was created


def test_cancel_voids_a_draft_the_user_has_not_decided_on(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    _watching_dot(factory, tmp_path)
    _trigger_dot(factory)
    with factory() as session:
        assert len(load_pending_signals(session, now=NOW)) == 1

    _post(factory, tmp_path, 3, T0 + timedelta(hours=13), "接近TP1位置了 先撤單", reply_to=2)
    _extract(factory, FakeAi([]))

    with factory() as session:
        assert load_pending_signals(session, now=NOW) == []
        revisions = list(
            session.scalars(select(NormalizedSignal).order_by(NormalizedSignal.revision))
        )
        assert [(r.revision, r.status) for r in revisions] == [(0, "VALIDATED"), (1, "CANCELLED")]
    assert "setup_draft_voided" in _events(factory)


def test_an_approved_draft_is_only_notified_never_cancelled(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    _watching_dot(factory, tmp_path)
    _trigger_dot(factory)
    with factory.begin() as session:
        signal = session.scalars(select(NormalizedSignal)).one()
        create_request(session, signal, nonce="n1", telegram_message_id=1)
        request_id = request_id_for_signal_row(signal.signal_row_id)
    with factory.begin() as session:
        record_decision(
            session,
            request_id=request_id,
            actor_user_id=1,
            callback_query_id="cb",
            action="APPROVE",
            now=NOW,
        )

    _post(factory, tmp_path, 3, T0 + timedelta(hours=13), "Tp🔥")  # no coin, no reply link
    _extract(factory, FakeAi([]))

    with factory() as session:
        revisions = list(session.scalars(select(NormalizedSignal)))
        assert [r.status for r in revisions] == ["VALIDATED"]  # the approved draft is untouched
    events = _events(factory)
    assert "setup_analyst_notice" in events
    assert "setup_draft_voided" not in events


def test_a_coinless_message_with_several_candidates_is_ambiguous_and_changes_nothing(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    _watching_dot(factory, tmp_path)
    _post(
        factory,
        tmp_path,
        3,
        T0 + timedelta(hours=12, minutes=30),
        "BTC 多\nEN:100-102\nSL:97\nTP:110-112",
    )
    btc = _setup_json(
        symbol="BTCUSDT",
        side="LONG",
        is_update_of_earlier_message=False,
        entry_zone_low=100,
        entry_zone_high=102,
        stop_loss=97,
        take_profits=[{"low": 110, "high": 112}],
        evidence_quotes=["EN:100-102"],
    )
    _extract(factory, FakeAi([btc]))
    before = _statuses(factory)

    _post(factory, tmp_path, 4, T0 + timedelta(hours=13), "Tp🔥")
    _extract(factory, FakeAi([]))

    assert _statuses(factory) == before
    assert "setup_status_ambiguous" in _events(factory)


def test_a_repeated_unchanged_setup_is_not_created_again(engine: Engine, tmp_path: Path) -> None:
    factory = create_session_factory(engine)
    _watching_dot(factory, tmp_path)
    _post(factory, tmp_path, 3, T0 + timedelta(hours=13), "DOT 繼續看空 區間不變")

    result = _extract(factory, FakeAi([_setup_json()]))

    assert result.created_count == 0
    with factory() as session:
        active = session.scalars(
            select(AnalysisSetup).where(AnalysisSetup.status == "WATCHING")
        ).one()
        assert active.valid_from == UPDATE_AT  # the monitoring clock was not restarted


def test_the_ai_can_flag_a_status_message_the_keywords_missed(
    engine: Engine, tmp_path: Path
) -> None:
    factory = create_session_factory(engine)
    _watching_dot(factory, tmp_path)
    _post(factory, tmp_path, 3, T0 + timedelta(hours=13), "拉太快了 這單不追了")
    ai = FakeAi(
        [
            {
                "summary_zh": "分析師放棄追這單",
                "message_kind": "CANCEL",
                "refers_to_symbol": "DOTUSDT",
                "setups": [],
            }
        ]
    )

    _extract(factory, ai)

    assert ("DOTUSDT", "INVALIDATED", "ANALYST_CANCEL") in _statuses(factory)
