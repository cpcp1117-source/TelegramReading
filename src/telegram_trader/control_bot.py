from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker
from telethon import Button, TelegramClient, events  # type: ignore[import-untyped]

from telegram_trader.binance_market_data import create_market_data_client
from telegram_trader.config import Settings, get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.models import NormalizedSignal, OutboxEvent, SignalDecisionRequest
from telegram_trader.outbox import OutboxConsumer, load_pending_events
from telegram_trader.risk_engine import RiskConfig, RiskEvaluation
from telegram_trader.signal_decisions import (
    DecisionAction,
    DecisionOutcome,
    DraftValues,
    EditableField,
    create_edit,
    create_request,
    find_request_by_nonce,
    load_current_draft,
    load_pending_signals,
    load_pending_signals_with_source,
    record_decision,
)
from telegram_trader.telegram_collector import reconnect_delay
from telegram_trader.telegram_readonly import prepare_session_path
from telegram_trader.trade_intent_pipeline import preview_trade_intent, risk_config_from_settings

LOGGER = logging.getLogger(__name__)

_EXECUTION_OUTBOX_CONSUMER_NAME = "control_bot"
_CALLBACK_SEP = ":"
_CALLBACK_PREFIX_SIGNAL = "signal"
_CALLBACK_PREFIX_CLOSE_ALL = "close_all"
_CALLBACK_PREFIX_EDIT = "edit"
_CLOSE_ALL_CONFIRM_WINDOW_SECONDS = 30
_EDIT_REPLY_WINDOW_SECONDS = 120
_EDIT_FIELD_LABELS: dict[EditableField, str] = {"STOP": "停損", "TAKE_PROFIT": "停利"}

_NO_EXECUTION_MESSAGE = (
    "✅ 已收到指令，但目前系統沒有可控制的執行元件"
    "（Risk Engine / Execution Gateway 要 Phase 6 才存在），"
    "這個指令目前只會被記錄，不會有任何實際交易動作。"
)
_UNKNOWN_COMMAND_MESSAGE = (
    "無法辨識的指令。可用指令：/status /signals /pause /resume /close /close_all"
)
_OUTCOME_MESSAGES: dict[DecisionOutcome, str] = {
    "APPROVED": "✅ 已核准（僅記錄，Phase 4 不會有任何自動下單）",
    "REJECTED": "❌ 已拒絕",
    "REJECTED_STALE": "⚠️ 這個請求已經被處理過，或訊號已經有更新版本，此次操作不生效",
    "REJECTED_EXPIRED": "⏰ 已過期，此次操作不生效",
}


def generate_nonce() -> str:
    return secrets.token_hex(16)


def encode_signal_callback(nonce: str, action: DecisionAction) -> bytes:
    """Telegram caps callback data at 64 bytes -- too small to also carry the

    64-character `request_id`, so only the nonce (unique, 128 bits) travels
    in the button; a tap is resolved back to its request by nonce alone
    (`signal_decisions.find_request_by_nonce`).
    """
    parts = (_CALLBACK_PREFIX_SIGNAL, nonce, action)
    return _CALLBACK_SEP.join(parts).encode()


def decode_signal_callback(data: bytes) -> tuple[str, DecisionAction] | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    parts = text.split(_CALLBACK_SEP)
    if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX_SIGNAL:
        return None
    _, nonce, action = parts
    if action not in ("APPROVE", "REJECT"):
        return None
    return nonce, cast(DecisionAction, action)


def encode_close_all_callback(nonce: str) -> bytes:
    return f"{_CALLBACK_PREFIX_CLOSE_ALL}{_CALLBACK_SEP}{nonce}".encode()


def decode_close_all_callback(data: bytes) -> str | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    parts = text.split(_CALLBACK_SEP)
    if len(parts) != 2 or parts[0] != _CALLBACK_PREFIX_CLOSE_ALL:
        return None
    return parts[1]


def encode_edit_field_callback(nonce: str, field: EditableField) -> bytes:
    """`"edit:<nonce>:<STOP|TAKE_PROFIT>"` -- well under the 64-byte cap

    (worst case ~44 bytes), same nonce-only resolution as the signal
    callback above.
    """
    parts = (_CALLBACK_PREFIX_EDIT, nonce, field)
    return _CALLBACK_SEP.join(parts).encode()


def decode_edit_field_callback(data: bytes) -> tuple[str, EditableField] | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    parts = text.split(_CALLBACK_SEP)
    if len(parts) != 3 or parts[0] != _CALLBACK_PREFIX_EDIT:
        return None
    _, nonce, field = parts
    if field not in ("STOP", "TAKE_PROFIT"):
        return None
    return nonce, cast(EditableField, field)


def _format_decimal(value: Decimal) -> str:
    """Fixed-point, trailing-zeros-stripped display -- never scientific notation.

    Postgres's `Numeric(20, 8)` column pads a stored value out to its full
    declared scale on read-back (e.g. `10` -> `Decimal("10.00000000")`), so
    the same logical value can otherwise print differently depending on
    whether it was just typed (in-memory, unrounded) or round-tripped
    through the database (carried forward from a prior edit) -- a real
    inconsistency caught via live testing, not just a style preference.
    """
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_preview(preview: RiskEvaluation, *, leverage: int) -> str:
    """Renders `preview_trade_intent`'s result -- read-only, informational only.

    Sizing (`quantity`/margin) is independent of stop distance since the
    2026-09-25 flat-sizing change (see risk_engine.py's `RiskConfig.position_size_pct`
    docstring), so this never changes when the user edits stop/take-profit
    afterward -- no re-preview is needed on an edit.
    """
    quantity = preview.quantity
    entry_price_used = preview.entry_price_used
    if preview.verdict != "APPROVED" or quantity is None or entry_price_used is None:
        reasons = "、".join(preview.reason_codes) or "未知原因"
        return f"⚠️ 目前無法預覽下單數量（{reasons}），核准後系統仍會重新計算一次。"
    notional_usdt = quantity * entry_price_used
    margin_usdt = notional_usdt / Decimal(leverage)
    lines = [
        f"預覽進場價: {_format_decimal(entry_price_used)}",
        f"預覽數量: {_format_decimal(quantity)}",
        f"預覽保證金: {_format_decimal(margin_usdt)} USDT"
        f"（名義本金: {_format_decimal(notional_usdt)} USDT, {leverage}x）",
    ]
    if preview.computed_stop_price is not None:
        lines.append(f"預覽停損價: {_format_decimal(preview.computed_stop_price)}")
    if preview.advisory_codes:
        lines.append("⚠️ 提醒（不會擋單）: " + "、".join(preview.advisory_codes))
    return "\n".join(lines)


def format_signal_notification(
    signal: NormalizedSignal,
    *,
    draft: DraftValues | None = None,
    preview: RiskEvaluation | None = None,
    leverage: int | None = None,
) -> str:
    """`draft` is only ever passed after an edit -- the initial notification

    always shows the parser's own unedited values. `preview`/`leverage` are
    the pre-approval sizing preview (`trade_intent_pipeline.preview_trade_intent`)
    -- omitted entirely (not just blank) when the caller has none to show,
    e.g. a signal whose channel/status never produces one.
    """
    stop_value = draft.stop_value if draft is not None else signal.stop_value
    take_profits = (
        draft.take_profits
        if draft is not None
        else [Decimal(str(value)) for value in signal.take_profits]
    )
    lines = [
        "📡 新訊號待核准" if signal.status == "NEW" else "📡 訊號待核准",
        f"symbol: {signal.symbol or '(未知)'}",
        f"side: {signal.side or '(未知)'}",
        f"status: {signal.status}",
        f"entry_type: {signal.entry_type or '-'}",
        f"stop_value: {_format_decimal(stop_value) if stop_value is not None else '-'}",
        (
            "take_profits: "
            + (", ".join(_format_decimal(tp) for tp in take_profits) if take_profits else "-")
        ),
        f"stop_origin: {signal.stop_origin}",
    ]
    if preview is not None and leverage is not None:
        lines.append(_format_preview(preview, leverage=leverage))
    if draft is not None:
        lines.append("✏️ 停損/停利已依你的修改顯示（尚未核准）。")
    if signal.status == "NEW":
        lines.append(
            "⚠️ symbol 尚未經市場資料驗證（動態範圍頻道），此核准僅供記錄，不會有任何自動下單。"
        )
    return "\n".join(lines)


_EXECUTION_EVENT_TITLES: dict[str, str] = {
    "execution_skipped_expired": "⏰ 訊號已過期，未執行下單",
    "execution_cancelled": "🚫 下單已取消",
    "execution_entry_submitted": "📤 進場單已送出",
    "execution_entry_not_filled": "⚠️ 進場單逾時未成交",
    "execution_entry_filled": "✅ 進場單已成交",
    "execution_protection_retrying": "⚠️ 停損單第一次掛單失敗，5 秒內重試一次",
    "execution_protection_confirmed": "🛡️ 停損單已掛上",
    "execution_protection_failed": "❌ 停損單掛單失敗——目前沒有保護，請自行設定停損",
    "execution_take_profit_retrying": "⚠️ 停利單第一次掛單失敗，5 秒內重試一次",
    "execution_take_profit_confirmed": "🎯 停利單已掛上",
    "execution_take_profit_failed": "⚠️ 停利單掛單失敗",
    "execution_unexpected_error": "🔥 執行過程發生未預期錯誤",
}


def format_execution_notification(event: OutboxEvent) -> str:
    """Renders one Execution Gateway outbox event as a Telegram message

    (added 2026-09-29, explicit user request: every order/protection/take-profit
    success or failure must be notified). `event.payload` is whatever
    `execution_gateway._notify` recorded -- rendered generically (key: value
    per line) rather than one bespoke formatter per event type, so a new
    event type is readable here without a matching code change.
    """
    title = _EXECUTION_EVENT_TITLES.get(event.event_type, f"ℹ️ {event.event_type}")
    lines = [title, f"intent_id: {event.aggregate_id[:12]}..."]
    for key, value in event.payload.items():
        if value is None:
            continue
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def format_signals_list(signals: Sequence[NormalizedSignal]) -> str:
    if not signals:
        return "目前沒有待處理的訊號。"
    lines = [f"待處理訊號（{len(signals)} 筆）："]
    for signal in signals:
        lines.append(f"- {signal.symbol or '?'} {signal.side or '?'} [{signal.status}]")
    return "\n".join(lines)


def format_status(pending_count: int, last_poll_at: datetime | None) -> str:
    poll_text = last_poll_at.isoformat() if last_poll_at else "尚未執行過"
    return f"待處理訊號：{pending_count} 筆\n上次掃描時間：{poll_text}"


def _signal_decision_buttons(nonce: str) -> list[list[Any]]:
    return [
        [
            Button.inline("✅ Approve", encode_signal_callback(nonce, "APPROVE")),
            Button.inline("❌ Reject", encode_signal_callback(nonce, "REJECT")),
        ],
        [
            Button.inline("✏️ 編輯停損", encode_edit_field_callback(nonce, "STOP")),
            Button.inline("✏️ 編輯停利", encode_edit_field_callback(nonce, "TAKE_PROFIT")),
        ],
    ]


@dataclass(frozen=True, slots=True)
class _PendingEdit:
    request_id: str
    field: EditableField
    expires_at: datetime


class ControlBot:
    def __init__(
        self,
        client: Any,
        session_factory: sessionmaker[Session],
        *,
        bot_token: str,
        allowlisted_user_id: int,
        poll_interval_seconds: float = 60.0,
        execution_poll_interval_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
        risk_config: RiskConfig | None = None,
        market_data_client: Any | None = None,
    ) -> None:
        """`risk_config`/`market_data_client` are optional: when either is `None`

        (e.g. most existing tests, which construct a `ControlBot` without a
        real Binance client), the pending-signal notification is sent
        without a sizing preview rather than raising -- the approve/reject
        flow itself has never depended on this preview existing.

        `execution_poll_interval_seconds` defaults tighter than
        `poll_interval_seconds` (10s vs 60s): an order/protection/take-profit
        outcome is time-sensitive in a way a brand-new signal notification
        is not (added 2026-09-29, "success or failure must always send a
        message" -- the point is largely defeated if it arrives a minute
        late).
        """
        self._client = client
        self._session_factory = session_factory
        self._bot_token = bot_token
        self._allowlisted_user_id = allowlisted_user_id
        self._poll_interval_seconds = poll_interval_seconds
        self._execution_poll_interval_seconds = execution_poll_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._risk_config = risk_config
        self._market_data_client = market_data_client
        self._last_poll_at: datetime | None = None
        self._pending_close_all_nonce: str | None = None
        self._pending_close_all_expires_at: datetime | None = None
        self._pending_edit: _PendingEdit | None = None

    def _is_allowlisted(self, sender_id: int | None) -> bool:
        return sender_id is not None and sender_id == self._allowlisted_user_id

    async def notify_pending_signals(self) -> int:
        """Send one notification per pending signal, then record the request.

        A signal is only ever notified once: `load_pending_signals` excludes
        anything with an existing `signal_decision_requests` row, and that
        row is only created after the send below succeeds. It also excludes
        anything already past its own `expires_at`, so a `parser_version`
        bump re-parsing the entire real backlog does not flood a stale
        notification for a trade idea from weeks ago.
        """
        count = 0
        with self._session_factory() as session:
            pending = load_pending_signals_with_source(session, now=self._clock())
            for signal, raw_message in pending:
                preview: RiskEvaluation | None = None
                if self._risk_config is not None and self._market_data_client is not None:
                    try:
                        preview = preview_trade_intent(
                            signal,
                            raw_message,
                            config=self._risk_config,
                            market_data_client=self._market_data_client,
                            now=self._clock(),
                        )
                    except Exception:
                        LOGGER.exception(
                            "signal preview failed unexpectedly; notifying without one",
                            extra={"context": {"signal_row_id": signal.signal_row_id}},
                        )
                nonce = generate_nonce()
                message = await self._client.send_message(
                    self._allowlisted_user_id,
                    format_signal_notification(
                        signal,
                        preview=preview,
                        leverage=self._risk_config.leverage if self._risk_config else None,
                    ),
                    buttons=_signal_decision_buttons(nonce),
                )
                create_request(session, signal, nonce=nonce, telegram_message_id=message.id)
                session.commit()
                count += 1
        self._last_poll_at = self._clock()
        return count

    async def _poll_signals_forever(self) -> None:
        attempt = 0
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                await self.notify_pending_signals()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = reconnect_delay(attempt)
                LOGGER.exception(
                    "control bot signal poll failed unexpectedly; retrying",
                    extra={"context": {"retry_seconds": delay}},
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def notify_execution_events(self) -> int:
        """Forwards every not-yet-delivered Execution Gateway outbox event as a

        Telegram message (added 2026-09-29, explicit user request). Execution
        Gateway itself never touches Telegram -- the `outbox_events` table
        (Phase 1 infrastructure, reused here for the first time) is the only
        thing connecting the two, matching credential-handoff.md's boundary
        (Testnet key stays Execution-Gateway-only; Telegram stays
        Control-Bot-only).
        """
        count = 0
        with self._session_factory() as session:
            pending = load_pending_events(
                session,
                consumer_name=_EXECUTION_OUTBOX_CONSUMER_NAME,
                event_type_prefix="execution_",
            )
            for event in pending:
                await self._client.send_message(
                    self._allowlisted_user_id, format_execution_notification(event)
                )
                consumer = OutboxConsumer(self._session_factory, _EXECUTION_OUTBOX_CONSUMER_NAME)
                consumer.acknowledge(event.event_id)
                count += 1
        return count

    async def _poll_execution_events_forever(self) -> None:
        attempt = 0
        while True:
            await asyncio.sleep(self._execution_poll_interval_seconds)
            try:
                await self.notify_execution_events()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                delay = reconnect_delay(attempt)
                LOGGER.exception(
                    "control bot execution-notification poll failed unexpectedly; retrying",
                    extra={"context": {"retry_seconds": delay}},
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def _on_message(self, event: Any) -> None:
        if not self._is_allowlisted(event.sender_id):
            LOGGER.warning(
                "control bot rejected unauthorized sender",
                extra={"context": {"sender_id": event.sender_id}},
            )
            return
        text = (getattr(event, "raw_text", None) or "").strip()
        if not text:
            return

        pending_edit = self._pending_edit
        if pending_edit is not None:
            self._pending_edit = None
            if self._clock() <= pending_edit.expires_at:
                await self._apply_pending_edit(event, pending_edit, text)
                return
            # Expired: fall through to normal command dispatch below so a
            # stale reply is never misinterpreted as an edit value.

        command = text.split()[0].lower().split("@")[0]

        if command == "/status":
            with self._session_factory() as session:
                pending_count = len(load_pending_signals(session))
            await event.reply(format_status(pending_count, self._last_poll_at))
        elif command == "/signals":
            with self._session_factory() as session:
                pending = load_pending_signals(session)
            await event.reply(format_signals_list(pending))
        elif command == "/close_all":
            nonce = generate_nonce()
            self._pending_close_all_nonce = nonce
            self._pending_close_all_expires_at = self._clock() + timedelta(
                seconds=_CLOSE_ALL_CONFIRM_WINDOW_SECONDS
            )
            await event.reply(
                "⚠️ close_all 需要二次確認，請在 30 秒內按下確認。",
                buttons=[[Button.inline("確認 close_all", encode_close_all_callback(nonce))]],
            )
        elif command in ("/pause", "/resume", "/close"):
            await event.reply(_NO_EXECUTION_MESSAGE)
        else:
            await event.reply(_UNKNOWN_COMMAND_MESSAGE)

    async def _apply_pending_edit(self, event: Any, pending: _PendingEdit, text: str) -> None:
        try:
            value = Decimal(text.strip())
            if value <= 0:
                raise InvalidOperation("value must be positive")
        except InvalidOperation:
            await event.reply("⚠️ 格式錯誤，請輸入正數數字，或重新點擊編輯按鈕。")
            return

        with self._session_factory.begin() as session:
            request = session.get(SignalDecisionRequest, pending.request_id)
            if request is None:
                signal = None
                draft = None
                nonce = None
            else:
                new_value: Decimal | list[Decimal] = value if pending.field == "STOP" else [value]
                draft = create_edit(session, request, field=pending.field, value=new_value)
                signal = session.get(NormalizedSignal, request.signal_row_id)
                nonce = request.nonce

        if signal is None or draft is None or nonce is None:
            await event.reply("⚠️ 找不到這個請求，可能已經過期或被清除，請重新查看 /signals。")
            return

        label = _EDIT_FIELD_LABELS[pending.field]
        await event.reply(
            f"✅ 已更新{label}草稿。\n\n{format_signal_notification(signal, draft=draft)}",
            buttons=_signal_decision_buttons(nonce),
        )

    async def _on_callback(self, event: Any) -> None:
        if not self._is_allowlisted(event.sender_id):
            LOGGER.warning(
                "control bot rejected unauthorized callback",
                extra={"context": {"sender_id": event.sender_id}},
            )
            return

        data = event.data
        close_all_nonce = decode_close_all_callback(data)
        if close_all_nonce is not None:
            still_valid = (
                self._pending_close_all_nonce == close_all_nonce
                and self._pending_close_all_expires_at is not None
                and self._clock() <= self._pending_close_all_expires_at
            )
            self._pending_close_all_nonce = None
            self._pending_close_all_expires_at = None
            if still_valid:
                await event.edit(_NO_EXECUTION_MESSAGE, buttons=None)
                await event.answer()
            else:
                await event.answer("已過期或無效，請重新輸入 /close_all", alert=True)
            return

        edit_decoded = decode_edit_field_callback(data)
        if edit_decoded is not None:
            edit_nonce, field = edit_decoded
            with self._session_factory() as session:
                request = find_request_by_nonce(session, edit_nonce)
                draft = load_current_draft(session, request) if request is not None else None
            if request is None or draft is None:
                await event.answer("找不到這個請求", alert=True)
                return
            self._pending_edit = _PendingEdit(
                request_id=request.request_id,
                field=field,
                expires_at=self._clock() + timedelta(seconds=_EDIT_REPLY_WINDOW_SECONDS),
            )
            label = _EDIT_FIELD_LABELS[field]
            if field == "STOP":
                current_value = (
                    _format_decimal(draft.stop_value) if draft.stop_value is not None else None
                )
            else:
                current_value = ", ".join(_format_decimal(tp) for tp in draft.take_profits) or None
            await event.answer()
            await event.reply(
                f"目前{label}為 {current_value if current_value is not None else '-'}。"
                f"請在 {_EDIT_REPLY_WINDOW_SECONDS} 秒內回覆新的{label}數值（純數字）。"
            )
            return

        decoded = decode_signal_callback(data)
        if decoded is None:
            await event.answer("無效的操作", alert=True)
            return
        nonce, action = decoded

        with self._session_factory.begin() as session:
            request = find_request_by_nonce(session, nonce)
            if request is None:
                outcome = None
            else:
                outcome = record_decision(
                    session,
                    request_id=request.request_id,
                    actor_user_id=event.sender_id,
                    callback_query_id=str(event.id),
                    action=action,
                )

        if outcome is None:
            await event.answer("找不到這個請求或 nonce 不符", alert=True)
            return
        await event.edit(_OUTCOME_MESSAGES[outcome], buttons=None)
        await event.answer()

    async def run_connection(self) -> None:
        await self._client.start(bot_token=self._bot_token)
        message_builder = events.NewMessage()
        callback_builder = events.CallbackQuery()
        self._client.add_event_handler(self._on_message, message_builder)
        self._client.add_event_handler(self._on_callback, callback_builder)
        try:
            await self._client.run_until_disconnected()
        finally:
            self._client.remove_event_handler(self._on_message, message_builder)
            self._client.remove_event_handler(self._on_callback, callback_builder)

    async def run_forever(self) -> None:
        poll_task = asyncio.create_task(self._poll_signals_forever())
        execution_poll_task = asyncio.create_task(self._poll_execution_events_forever())
        try:
            attempt = 0
            while True:
                try:
                    await self.run_connection()
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    delay = reconnect_delay(attempt)
                    LOGGER.warning(
                        "control bot disconnected; retrying",
                        extra={
                            "context": {
                                "error_type": type(error).__name__,
                                "retry_seconds": delay,
                            }
                        },
                    )
                    attempt += 1
                    await asyncio.sleep(delay)
        finally:
            poll_task.cancel()
            execution_poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task
            with contextlib.suppress(asyncio.CancelledError):
                await execution_poll_task


def create_bot_client(
    settings: Settings,
    client_factory: Callable[[str, int, str], Any] | None = None,
) -> Any:
    """Build a bot client without connecting or exposing credential values.

    Bot login still needs a Telegram API app id/hash pair (the same one the
    collector already uses) -- only the login method (`bot_token` instead
    of phone/OTP/2FA) differs.
    """
    if settings.environment != "control_bot":
        raise ValueError("Control Bot client is available only in control_bot mode")
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        raise ValueError("Telegram credentials are unavailable")
    if settings.control_bot_token is None or settings.control_bot_allowlisted_user_id is None:
        raise ValueError("Control Bot credentials are unavailable")

    session_path = prepare_session_path(settings.control_bot_session_path)
    api_hash = settings.telegram_api_hash.get_secret_value()
    factory = client_factory or cast(
        Callable[[str, int, str], Any],
        TelegramClient,
    )
    return factory(str(session_path), settings.telegram_api_id, api_hash)


async def _run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    if settings.control_bot_token is None or settings.control_bot_allowlisted_user_id is None:
        raise ValueError("Control Bot credentials are unavailable")

    risk_config: RiskConfig | None = None
    try:
        risk_config = risk_config_from_settings(settings)
    except ValueError:
        LOGGER.warning(
            "RISK_EQUITY_BASELINE_USDT is not configured; pending-signal notifications "
            "will be sent without a sizing preview until it is set"
        )
    market_data_client = create_market_data_client(settings) if risk_config is not None else None

    client = create_bot_client(settings)
    bot = ControlBot(
        client,
        session_factory,
        bot_token=settings.control_bot_token.get_secret_value(),
        allowlisted_user_id=settings.control_bot_allowlisted_user_id,
        poll_interval_seconds=settings.control_bot_poll_interval_seconds,
        risk_config=risk_config,
        market_data_client=market_data_client,
    )
    try:
        await bot.run_forever()
        return 0
    finally:
        await client.disconnect()
        if market_data_client is not None:
            market_data_client.close()
        engine.dispose()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
