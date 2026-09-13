from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from telegram_trader.control_bot import (
    _NO_EXECUTION_MESSAGE,
    _UNKNOWN_COMMAND_MESSAGE,
    ControlBot,
    _format_decimal,
    _PendingEdit,
    decode_close_all_callback,
    decode_edit_field_callback,
    decode_signal_callback,
    encode_close_all_callback,
    encode_edit_field_callback,
    encode_signal_callback,
    format_signal_notification,
    format_signals_list,
    format_status,
    generate_nonce,
)
from telegram_trader.models import NormalizedSignal
from telegram_trader.signal_decisions import DraftValues

NOW = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)


class FakeBotClient:
    def __init__(self) -> None:
        self.started_with_token: str | None = None
        self.handlers: list[tuple[object, object]] = []
        self.disconnected = False

    async def start(self, bot_token: str) -> object:
        self.started_with_token = bot_token
        return self

    def add_event_handler(self, callback: object, builder: object) -> None:
        self.handlers.append((callback, builder))

    def remove_event_handler(self, callback: object, builder: object) -> None:
        self.handlers.remove((callback, builder))

    async def run_until_disconnected(self) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True


@dataclass
class FakeEvent:
    """Mirrors Telethon's `CallbackQuery.Event` shape: the query id is `event.id`

    directly (a convenience property over the raw `UpdateBotCallbackQuery`,
    whose own field is `query_id`, not `id` -- a mismatch that caused a real
    `AttributeError` in production before `id` replaced `query.id` here).
    """

    sender_id: int | None
    raw_text: str | None = None
    data: bytes | None = None
    id: int = 1
    replies: list[tuple[str, object]] = field(default_factory=list)
    answers: list[tuple[str | None, bool]] = field(default_factory=list)
    edits: list[tuple[str, object]] = field(default_factory=list)

    async def reply(self, text: str, buttons: object = None) -> None:
        self.replies.append((text, buttons))

    async def answer(self, text: str | None = None, alert: bool = False) -> None:
        self.answers.append((text, alert))

    async def edit(self, text: str, buttons: object = None) -> None:
        self.edits.append((text, buttons))


def _make_bot(*, clock: Callable[[], datetime] | None = None) -> ControlBot:
    return ControlBot(
        FakeBotClient(),
        session_factory=None,  # type: ignore[arg-type]
        bot_token="placeholder",
        allowlisted_user_id=555,
        poll_interval_seconds=0.01,
        clock=clock or (lambda: NOW),
    )


def _signal(**overrides: object) -> NormalizedSignal:
    values: dict[str, object] = {
        "signal_row_id": "row-1",
        "signal_id": "sig-1",
        "revision": 0,
        "raw_message_id": "raw-1",
        "normalizer_version": "v2",
        "channel_id": 2439599598,
        "topic_id": None,
        "status": "NEW",
        "symbol": "ARB",
        "side": "SHORT",
        "entry_type": "MARKET",
        "entry_values": [],
        "stop_value": None,
        "stop_origin": "DEFAULT_ROE_30",
        "take_profits": [],
        "evidence_spans": {},
        "link_method": None,
        "parser_version": "v1",
        "expires_at": NOW,
    }
    values.update(overrides)
    return NormalizedSignal(**values)


# --- pure encode/decode ---


def test_signal_callback_round_trip() -> None:
    """Must fit Telegram's 64-byte callback-data cap even with a full-length nonce."""
    encoded = encode_signal_callback("nonce-1", "APPROVE")
    assert len(encoded) <= 64
    assert decode_signal_callback(encoded) == ("nonce-1", "APPROVE")


def test_signal_callback_round_trip_with_realistic_nonce_stays_under_limit() -> None:
    encoded = encode_signal_callback(generate_nonce(), "APPROVE")
    assert len(encoded) <= 64


def test_signal_callback_rejects_garbage() -> None:
    assert decode_signal_callback(b"not-valid-data") is None
    assert decode_signal_callback(b"signal:only") is None
    assert decode_signal_callback(b"signal:nonce:GARBAGE") is None
    assert decode_signal_callback(b"close_all:nonce:req") is None


def test_close_all_callback_round_trip() -> None:
    encoded = encode_close_all_callback("nonce-1")
    assert decode_close_all_callback(encoded) == "nonce-1"


def test_close_all_callback_rejects_signal_data() -> None:
    encoded = encode_signal_callback("nonce-1", "APPROVE")
    assert decode_close_all_callback(encoded) is None


def test_edit_field_callback_round_trip_stop() -> None:
    encoded = encode_edit_field_callback("nonce-1", "STOP")
    assert len(encoded) <= 64
    assert decode_edit_field_callback(encoded) == ("nonce-1", "STOP")


def test_edit_field_callback_round_trip_take_profit_with_realistic_nonce() -> None:
    encoded = encode_edit_field_callback(generate_nonce(), "TAKE_PROFIT")
    assert len(encoded) <= 64
    decoded = decode_edit_field_callback(encoded)
    assert decoded is not None
    assert decoded[1] == "TAKE_PROFIT"


def test_edit_field_callback_rejects_garbage() -> None:
    assert decode_edit_field_callback(b"not-valid-data") is None
    assert decode_edit_field_callback(b"edit:only") is None
    assert decode_edit_field_callback(b"edit:nonce:GARBAGE") is None
    assert decode_edit_field_callback(b"signal:nonce:APPROVE") is None


def test_generate_nonce_is_unique_and_nonempty() -> None:
    first = generate_nonce()
    second = generate_nonce()
    assert first != second
    assert len(first) > 0


# --- formatting ---


def test_format_signal_notification_flags_dynamic_scope() -> None:
    text = format_signal_notification(_signal(status="NEW"))
    assert "ARB" in text
    assert "SHORT" in text
    assert "尚未經市場資料驗證" in text


def test_format_signal_notification_validated_has_no_dynamic_scope_warning() -> None:
    text = format_signal_notification(_signal(status="VALIDATED", symbol="BTCUSDT"))
    assert "尚未經市場資料驗證" not in text


def test_format_decimal_strips_trailing_zeros_without_scientific_notation() -> None:
    """Postgres's Numeric(20,8) pads a stored value to full scale on read-back

    (e.g. `10` -> `Decimal("10.00000000")`) -- caught via live testing as a
    real display inconsistency between a freshly-typed value and one
    carried forward through the database. This must render identically.
    """
    assert _format_decimal(Decimal("10.00000000")) == "10"
    assert _format_decimal(Decimal("10")) == "10"
    assert _format_decimal(Decimal("58000.50000000")) == "58000.5"
    assert _format_decimal(Decimal("0.00000000")) == "0"


def test_format_signal_notification_strips_trailing_zeros_from_decimal() -> None:
    text = format_signal_notification(
        _signal(stop_value=Decimal("10.00000000"), take_profits=["20.00000000"])
    )
    assert "stop_value: 10\n" in text
    assert "10.00000000" not in text
    assert "take_profits: 20\n" in text


def test_format_signal_notification_shows_original_values_without_draft() -> None:
    text = format_signal_notification(_signal(stop_value=Decimal("59000"), take_profits=["62000"]))
    assert "59000" in text
    assert "62000" in text
    assert "已依你的修改顯示" not in text


def test_format_signal_notification_shows_draft_values_and_marker() -> None:
    draft = DraftValues(stop_value=Decimal("58000"), take_profits=[Decimal("63000")])
    text = format_signal_notification(
        _signal(stop_value=Decimal("59000"), take_profits=["62000"]), draft=draft
    )
    assert "58000" in text
    assert "63000" in text
    assert "59000" not in text
    assert "已依你的修改顯示" in text


def test_format_signals_list_empty() -> None:
    assert "沒有待處理" in format_signals_list([])


def test_format_signals_list_counts_entries() -> None:
    text = format_signals_list([_signal(), _signal(signal_row_id="row-2", signal_id="sig-2")])
    assert "2" in text


def test_format_status_reports_no_poll_yet() -> None:
    assert "尚未執行過" in format_status(0, None)


def test_format_status_reports_last_poll_time() -> None:
    text = format_status(3, NOW)
    assert "3" in text
    assert NOW.isoformat() in text


# --- allowlist enforcement (NFR-007: 100% rejection, no DB access needed) ---


@pytest.mark.anyio
async def test_on_message_silently_rejects_unauthorized_sender() -> None:
    bot = _make_bot()
    event = FakeEvent(sender_id=999, raw_text="/status")

    await bot._on_message(event)

    assert event.replies == []


@pytest.mark.anyio
async def test_on_callback_silently_rejects_unauthorized_sender() -> None:
    bot = _make_bot()
    event = FakeEvent(sender_id=999, data=encode_signal_callback("n", "APPROVE"))

    await bot._on_callback(event)

    assert event.answers == []
    assert event.edits == []


@pytest.mark.anyio
async def test_on_message_unknown_command_from_allowlisted_user_gets_help() -> None:
    bot = _make_bot()
    event = FakeEvent(sender_id=555, raw_text="/nonsense")

    await bot._on_message(event)

    assert event.replies == [(_UNKNOWN_COMMAND_MESSAGE, None)]


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/pause", "/resume", "/close"])
async def test_on_message_stub_commands_explain_no_execution_component(command: str) -> None:
    bot = _make_bot()
    event = FakeEvent(sender_id=555, raw_text=command)

    await bot._on_message(event)

    assert event.replies == [(_NO_EXECUTION_MESSAGE, None)]


@pytest.mark.anyio
async def test_on_message_ignores_blank_text() -> None:
    bot = _make_bot()
    event = FakeEvent(sender_id=555, raw_text="   ")

    await bot._on_message(event)

    assert event.replies == []


# --- close_all double confirmation ---


@pytest.mark.anyio
async def test_close_all_requires_confirmation_button() -> None:
    bot = _make_bot()
    command_event = FakeEvent(sender_id=555, raw_text="/close_all")

    await bot._on_message(command_event)

    assert len(command_event.replies) == 1
    text, buttons = command_event.replies[0]
    assert "二次確認" in text
    assert buttons is not None


@pytest.mark.anyio
async def test_close_all_confirmation_within_window_succeeds() -> None:
    clock_value = {"now": NOW}
    bot = _make_bot(clock=lambda: clock_value["now"])
    await bot._on_message(FakeEvent(sender_id=555, raw_text="/close_all"))
    nonce = bot._pending_close_all_nonce
    assert nonce is not None

    callback_event = FakeEvent(sender_id=555, data=encode_close_all_callback(nonce))
    await bot._on_callback(callback_event)

    assert callback_event.edits == [(_NO_EXECUTION_MESSAGE, None)]
    assert bot._pending_close_all_nonce is None


@pytest.mark.anyio
async def test_close_all_confirmation_after_window_is_rejected() -> None:
    clock_value = {"now": NOW}
    bot = _make_bot(clock=lambda: clock_value["now"])
    await bot._on_message(FakeEvent(sender_id=555, raw_text="/close_all"))
    nonce = bot._pending_close_all_nonce
    assert nonce is not None
    clock_value["now"] = NOW + timedelta(seconds=999)

    callback_event = FakeEvent(sender_id=555, data=encode_close_all_callback(nonce))
    await bot._on_callback(callback_event)

    assert callback_event.edits == []
    assert callback_event.answers and callback_event.answers[0][1] is True


@pytest.mark.anyio
async def test_close_all_confirmation_with_wrong_nonce_is_rejected() -> None:
    bot = _make_bot()
    await bot._on_message(FakeEvent(sender_id=555, raw_text="/close_all"))

    callback_event = FakeEvent(sender_id=555, data=encode_close_all_callback("wrong-nonce"))
    await bot._on_callback(callback_event)

    assert callback_event.edits == []
    assert callback_event.answers and callback_event.answers[0][1] is True


@pytest.mark.anyio
async def test_signal_callback_with_unknown_request_answers_without_crash() -> None:
    bot = _make_bot()
    # request_id "unknown" cannot exist without a DB, but the lookup path is
    # exercised via record_decision's own integration tests; here we only
    # confirm garbage callback data is rejected before any DB access.
    event = FakeEvent(sender_id=555, data=b"garbage")

    await bot._on_callback(event)

    assert event.answers == [("無效的操作", True)]


# --- editable order drafts (stop-loss/take-profit) ---


@pytest.mark.anyio
async def test_on_message_pending_edit_invalid_format_replies_error_and_clears_state() -> None:
    bot = _make_bot()
    bot._pending_edit = _PendingEdit(
        request_id="req-1", field="STOP", expires_at=NOW + timedelta(seconds=60)
    )
    event = FakeEvent(sender_id=555, raw_text="not-a-number")

    await bot._on_message(event)

    assert bot._pending_edit is None
    assert len(event.replies) == 1
    assert "格式錯誤" in event.replies[0][0]


@pytest.mark.anyio
async def test_on_message_pending_edit_rejects_non_positive_value() -> None:
    bot = _make_bot()
    bot._pending_edit = _PendingEdit(
        request_id="req-1", field="STOP", expires_at=NOW + timedelta(seconds=60)
    )
    event = FakeEvent(sender_id=555, raw_text="-5")

    await bot._on_message(event)

    assert bot._pending_edit is None
    assert "格式錯誤" in event.replies[0][0]


@pytest.mark.anyio
async def test_on_message_expired_pending_edit_falls_through_to_normal_dispatch() -> None:
    clock_value = {"now": NOW}
    bot = _make_bot(clock=lambda: clock_value["now"])
    bot._pending_edit = _PendingEdit(
        request_id="req-1", field="STOP", expires_at=NOW - timedelta(seconds=1)
    )
    event = FakeEvent(sender_id=555, raw_text="/nonsense")

    await bot._on_message(event)

    assert bot._pending_edit is None
    assert event.replies == [(_UNKNOWN_COMMAND_MESSAGE, None)]


@pytest.mark.anyio
async def test_on_callback_unauthorized_sender_does_not_set_pending_edit() -> None:
    bot = _make_bot()
    event = FakeEvent(sender_id=999, data=encode_edit_field_callback("n", "STOP"))

    await bot._on_callback(event)

    assert bot._pending_edit is None
    assert event.answers == []


# --- background poll task lifecycle (mirrors test_telegram_collector.py's convention) ---


@pytest.mark.anyio
async def test_poll_signals_forever_retries_after_unexpected_error() -> None:
    bot = _make_bot()
    calls = {"count": 0}

    async def flaky_notify() -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic failure")
        return 0

    bot.notify_pending_signals = flaky_notify  # type: ignore[method-assign]

    task = asyncio.create_task(bot._poll_signals_forever())
    for _ in range(200):
        if calls["count"] >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls["count"] >= 2


@pytest.mark.anyio
async def test_run_forever_cancels_poll_task_cleanly() -> None:
    bot = _make_bot()

    async def no_op_notify() -> int:
        return 0

    bot.notify_pending_signals = no_op_notify  # type: ignore[method-assign]

    async def blocking_forever() -> None:
        await asyncio.Event().wait()

    bot.run_connection = blocking_forever  # type: ignore[method-assign]

    before = asyncio.all_tasks()
    task = asyncio.create_task(bot.run_forever())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert asyncio.all_tasks() - before == set()
