from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker
from telethon import Button, TelegramClient, events  # type: ignore[import-untyped]

from telegram_trader.binance_market_data import create_market_data_client
from telegram_trader.config import Settings, get_settings
from telegram_trader.daily_loss import (
    DAILY_LOSS_EVENT_TYPE,
    active_pause,
    daily_loss_pct,
    record_pause_decision,
)
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.draft_edit import (
    DraftEditError,
    EntryEdit,
    parse_entry_input,
    parse_size_input,
)
from telegram_trader.logging_config import configure_logging
from telegram_trader.models import (
    NormalizedSignal,
    OutboxEvent,
    SignalDecisionRequest,
    TelegramMessageVersion,
)
from telegram_trader.outbox import (
    OutboxConsumer,
    find_event_by_id_prefix,
    load_events_due_for_reminder,
    load_pending_events,
)
from telegram_trader.risk_engine import RiskConfig, RiskEvaluation
from telegram_trader.signal_decisions import (
    DecisionAction,
    DecisionOutcome,
    DraftValues,
    EditableField,
    create_edit,
    create_request,
    draft_from_signal,
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
# Receipts under these names mean "user marked it handled" / "reminder already sent"
# (2026-09-29, explicit user request: a failure not handled within 5 minutes is sent
# once more). Reusing outbox_delivery_receipts keeps this schema-free.
_EXECUTION_HANDLED_CONSUMER_NAME = "control_bot_handled"
_EXECUTION_REMINDER_CONSUMER_NAME = "control_bot_reminder"
_FAILURE_REMINDER_DELAY = timedelta(minutes=5)
# Failures the user must act on. `*_retrying` is excluded: its final outcome follows.
_EXECUTION_FAILURE_EVENT_TYPES = frozenset(
    {
        "execution_skipped_expired",
        "execution_cancelled",
        "execution_entry_failed",
        "execution_entry_not_filled",
        "execution_protection_failed",
        "execution_take_profit_failed",
        "execution_unexpected_error",
        "execution_risk_rejected",
        "execution_leftover_cancel_failed",
        "execution_worker_error",
    }
)
# Outbox event-type prefixes the Control Bot forwards to the user: Execution Gateway outcomes and
# the analysis-setup pipeline's (`setup_*`) heads-up / draft-created / closed notices.
_FORWARDED_EVENT_PREFIXES = ("execution_", "setup_")
_HANDLED_CALLBACK_ID_CHARS = 32
_CALLBACK_SEP = ":"
_CALLBACK_PREFIX_SIGNAL = "signal"
_CALLBACK_PREFIX_CLOSE_ALL = "close_all"
_CALLBACK_PREFIX_EDIT = "edit"
_CALLBACK_PREFIX_HANDLED = "handled"
_CALLBACK_PREFIX_DAILY_LOSS = "dailyloss"
_TAIWAN = timezone(timedelta(hours=8))
_CLOSE_ALL_CONFIRM_WINDOW_SECONDS = 30
_EDIT_REPLY_WINDOW_SECONDS = 120
_EDIT_FIELD_LABELS: dict[EditableField, str] = {
    "STOP": "停損",
    "TAKE_PROFIT": "停利",
    "ENTRY": "進場價",
    "SIZE": "倉位比例",
}
_EDIT_FIELDS: tuple[str, ...] = ("STOP", "TAKE_PROFIT", "ENTRY", "SIZE")

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
    """`"edit:<nonce>:<STOP|TAKE_PROFIT|ENTRY|SIZE>"` -- well under the 64-byte cap

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
    if field not in _EDIT_FIELDS:
        return None
    return nonce, cast(EditableField, field)


def encode_handled_callback(event_id: str) -> bytes:
    """`"handled:<first 32 hex chars of event_id>"` -- 40 bytes, under Telegram's
    64-byte cap; resolved back via `outbox.find_event_by_id_prefix`.
    """
    prefix = event_id[:_HANDLED_CALLBACK_ID_CHARS]
    return f"{_CALLBACK_PREFIX_HANDLED}{_CALLBACK_SEP}{prefix}".encode()


def decode_handled_callback(data: bytes) -> str | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    parts = text.split(_CALLBACK_SEP)
    if len(parts) != 2 or parts[0] != _CALLBACK_PREFIX_HANDLED or not parts[1]:
        return None
    return parts[1]


def _handled_buttons(event_id: str) -> list[list[Any]]:
    return [[Button.inline("✅ 我已處理", encode_handled_callback(event_id))]]


def encode_daily_loss_callback(event_id: str, decision: str) -> bytes:
    """`"dailyloss:<first 32 hex of event_id>:<STOP|CONTINUE>"` -- 52 bytes, under 64."""
    prefix = event_id[:_HANDLED_CALLBACK_ID_CHARS]
    return _CALLBACK_SEP.join((_CALLBACK_PREFIX_DAILY_LOSS, prefix, decision)).encode()


def decode_daily_loss_callback(data: bytes) -> tuple[str, str] | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    parts = text.split(_CALLBACK_SEP)
    if (
        len(parts) != 3
        or parts[0] != _CALLBACK_PREFIX_DAILY_LOSS
        or not parts[1]
        or parts[2] not in ("STOP", "CONTINUE")
    ):
        return None
    return parts[1], parts[2]


def _daily_loss_buttons(event_id: str) -> list[list[Any]]:
    return [
        [
            Button.inline("⛔ 停止今日開單", encode_daily_loss_callback(event_id, "STOP")),
            Button.inline("▶️ 繼續開單", encode_daily_loss_callback(event_id, "CONTINUE")),
        ]
    ]


def _taiwan_time(moment: datetime) -> str:
    return moment.astimezone(_TAIWAN).strftime("%m/%d %H:%M")


def format_daily_loss_notification(event: OutboxEvent) -> str:
    """The kill-switch question (user decision 2026-10-04): stop today, or keep going."""
    payload = event.payload
    day_start = datetime.fromisoformat(str(payload["day_start_utc"]))
    resume_at = _taiwan_time(day_start + timedelta(days=1))
    return "\n".join(
        [
            "🛑 今日虧損已達熔斷門檻",
            f"今日（台灣 {_taiwan_time(day_start)} 起）已實現淨損益: {payload['net_pnl']} USDT",
            f"虧損比例: {payload['loss_pct']}%（門檻 {payload['threshold_pct']}%，"
            f"資金基準 {payload['equity_usdt']} USDT）",
            "",
            f"要停止今日開單嗎？停止後到台灣時間 {resume_at} 前，"
            "新的核准都不會下單；已開的倉位和停損停利單不受影響。",
        ]
    )


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
    docstring), but it does depend on the entry price and the per-trade size the user can now
    edit, so the Control Bot recomputes this preview after every edit.
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
    default_size_pct: Decimal | None = None,
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
    entry_line = f"entry_type: {signal.entry_type or '-'}"
    if draft is not None and draft.entry_type == "MARKET":
        entry_line = "entry_type: MARKET（你改為市價）"
    elif draft is not None and draft.entry_type == "LIMIT" and draft.entry_value is not None:
        entry_line = f"entry_type: LIMIT @ {_format_decimal(draft.entry_value)}（你修改）"
    elif signal.entry_type in ("LIMIT", "RANGE") and signal.entry_values:
        entry_line += " @ " + ", ".join(
            _format_decimal(Decimal(str(v))) for v in signal.entry_values
        )
    size_pct = draft.position_size_pct if draft is not None else signal.suggested_size_pct
    if size_pct is not None:
        size_line: str | None = f"倉位: {_format_decimal(size_pct * 100)}% 資金作保證金" + (
            "（你修改）"
            if draft is not None and draft.position_size_pct is not None
            else "（預設）"
        )
    elif default_size_pct is not None:
        size_line = f"倉位: {_format_decimal(default_size_pct * 100)}% 資金作保證金（系統預設）"
    else:
        size_line = None
    lines = [
        "📡 新訊號待核准" if signal.status == "NEW" else "📡 訊號待核准",
        f"symbol: {signal.symbol or '(未知)'}",
        f"side: {signal.side or '(未知)'}",
        f"status: {signal.status}",
        entry_line,
        f"stop_value: {_format_decimal(stop_value) if stop_value is not None else '-'}",
        (
            "take_profits: "
            + (", ".join(_format_decimal(tp) for tp in take_profits) if take_profits else "-")
        ),
        f"stop_origin: {signal.stop_origin}",
    ]
    if size_line is not None:
        lines.append(size_line)
    if preview is not None and leverage is not None:
        lines.append(_format_preview(preview, leverage=leverage))
    if draft is not None:
        lines.append("✏️ 已依你的修改顯示（尚未核准）。")
    if signal.status == "NEW":
        lines.append(
            "⚠️ symbol 尚未經市場資料驗證（動態範圍頻道），此核准僅供記錄，不會有任何自動下單。"
        )
    return "\n".join(lines)


_EXECUTION_EVENT_TITLES: dict[str, str] = {
    "execution_skipped_expired": "⏰ 訊號已過期，未執行下單",
    "execution_cancelled": "🚫 下單已取消",
    "execution_entry_submitted": "📤 進場單已送出",
    "execution_entry_failed": (
        "❌ 進場單掛單失敗——不會自動重試。請到 Binance 確認是否有這筆單（client_order_id）"
    ),
    "execution_entry_not_filled": "⚠️ 進場單逾時未成交",
    "execution_entry_filled": "✅ 進場單已成交",
    "execution_protection_retrying": "⚠️ 停損單第一次掛單失敗，5 秒內重試一次",
    "execution_protection_confirmed": "🛡️ 停損單已掛上",
    "execution_protection_failed": "❌ 停損單掛單失敗——目前沒有保護，請自行設定停損",
    "execution_take_profit_retrying": "⚠️ 停利單第一次掛單失敗，5 秒內重試一次",
    "execution_take_profit_confirmed": "🎯 停利單已掛上",
    "execution_take_profit_failed": "⚠️ 停利單掛單失敗",
    "execution_unexpected_error": "🔥 執行過程發生未預期錯誤",
    "execution_risk_rejected": "🚫 風控未通過，這筆訊號不會下單",
    "execution_position_closed": "🏁 倉位已平倉",
    "execution_leftover_cancel_failed": (
        "⚠️ 平倉後剩下的停損/停利單取消失敗——請到 Binance 手動取消，否則之後同幣種的新倉可能被它平掉"
    ),
    "execution_worker_error": "🔥 自動執行服務出錯（會持續重試，恢復時會再通知）",
    "execution_worker_recovered": "✅ 自動執行服務已恢復",
    DAILY_LOSS_EVENT_TYPE: "🛑 今日虧損已達熔斷門檻",
}
_SETUP_EVENT_TITLES: dict[str, str] = {
    "setup_created": "🧠 AI 整理出一個可監控的情境（已開始監控，尚未開單）",
    "setup_zone_touched": "👀 價格碰到進場區",
    "setup_triggered": "✅ 條件確認——開單草稿已建立",
    "setup_invalidated": "🚫 情境失效，停止監控",
    "setup_expired": "⌛ 情境已過期，停止監控",
    "setup_worker_error": "🔥 分析監控服務出錯（會持續重試，恢復時會再通知）",
    "setup_worker_recovered": "✅ 分析監控服務已恢復",
    "setup_extraction_failed": "⚠️ AI 整理這則分析訊息失敗（會自動重試；持續失敗請檢查 AI 登入）",
}
_SETUP_FIELD_LABELS: dict[str, str] = {
    "symbol": "幣種",
    "side": "方向",
    "confidence": "信心",
    "summary": "摘要",
    "entry_zone": "進場區",
    "entry_price": "預填進場價",
    "stop": "停損",
    "targets": "目標",
    "confirm_rule": "確認條件",
    "risk_notes": "風險提醒",
    "reason": "原因",
    "note": "說明",
    "step": "步驟",
    "error": "錯誤",
    "previous_error": "先前錯誤",
}
_REMINDER_HEADER = "⏰ 提醒：這則失敗通知 5 分鐘內尚未處理"
_HANDLED_FOOTER = "✅ 已標記為已處理"
_DAILY_LOSS_REMINDER_HEADER = "⏰ 提醒：每日虧損熔斷 5 分鐘內尚未選擇"


def format_setup_notification(event: OutboxEvent) -> str:
    """Renders one analysis-setup outbox event (`setup_*`) as a Telegram message."""
    title = _SETUP_EVENT_TITLES.get(event.event_type, f"ℹ️ {event.event_type}")
    lines = [title]
    for key, value in event.payload.items():
        if value is None or key == "setup_id":
            continue
        lines.append(f"{_SETUP_FIELD_LABELS.get(key, key)}: {value}")
    return "\n".join(lines)


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
        [
            Button.inline("✏️ 編輯進場價", encode_edit_field_callback(nonce, "ENTRY")),
            Button.inline("✏️ 編輯倉位", encode_edit_field_callback(nonce, "SIZE")),
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
            pause = active_pause(session, self._clock()) if pending else None
            loss_pct = (
                daily_loss_pct(session, self._clock(), self._risk_config.equity_baseline_usdt)
                if pending and self._risk_config is not None
                else Decimal(0)
            )
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
                            daily_realized_loss_pct=loss_pct,
                            draft=draft_from_signal(signal),
                        )
                    except Exception:
                        LOGGER.exception(
                            "signal preview failed unexpectedly; notifying without one",
                            extra={"context": {"signal_row_id": signal.signal_row_id}},
                        )
                nonce = generate_nonce()
                text = format_signal_notification(
                    signal,
                    preview=preview,
                    leverage=self._risk_config.leverage if self._risk_config else None,
                    default_size_pct=(
                        self._risk_config.position_size_pct if self._risk_config else None
                    ),
                )
                if pause is not None and pause.paused_until is not None:
                    text = (
                        f"⛔ 今日已停止開單（到台灣時間 {_taiwan_time(pause.paused_until)}），"
                        f"現在核准也不會下單\n\n{text}"
                    )
                message = await self._client.send_message(
                    self._allowlisted_user_id, text, buttons=_signal_decision_buttons(nonce)
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
            pending = [
                event
                for prefix in _FORWARDED_EVENT_PREFIXES
                for event in load_pending_events(
                    session,
                    consumer_name=_EXECUTION_OUTBOX_CONSUMER_NAME,
                    event_type_prefix=prefix,
                )
            ]
            for event in pending:
                is_failure = event.event_type in _EXECUTION_FAILURE_EVENT_TYPES
                is_setup = event.event_type.startswith("setup_")
                buttons: list[list[Any]] | None
                if event.event_type == DAILY_LOSS_EVENT_TYPE:
                    text = format_daily_loss_notification(event)
                    buttons = _daily_loss_buttons(event.event_id)
                else:
                    text = (
                        format_setup_notification(event)
                        if is_setup
                        else format_execution_notification(event)
                    )
                    buttons = _handled_buttons(event.event_id) if is_failure else None
                await self._client.send_message(self._allowlisted_user_id, text, buttons=buttons)
                consumer = OutboxConsumer(self._session_factory, _EXECUTION_OUTBOX_CONSUMER_NAME)
                consumer.acknowledge(event.event_id)
                count += 1
        return count

    async def remind_unhandled_failures(self) -> int:
        """Sends each failure notification once more if the user has not pressed
        "我已處理" within 5 minutes of its delivery (2026-09-29, explicit user
        request -- this is a semi-automatic bot: failures are the user's to handle,
        the bot only makes sure they are seen). Exactly one reminder per failure.
        """
        count = 0
        with self._session_factory() as session:
            due = load_events_due_for_reminder(
                session,
                event_types=_EXECUTION_FAILURE_EVENT_TYPES | {DAILY_LOSS_EVENT_TYPE},
                delivered_consumer=_EXECUTION_OUTBOX_CONSUMER_NAME,
                handled_consumer=_EXECUTION_HANDLED_CONSUMER_NAME,
                reminded_consumer=_EXECUTION_REMINDER_CONSUMER_NAME,
                delivered_before=self._clock() - _FAILURE_REMINDER_DELAY,
            )
            for event in due:
                if event.event_type == DAILY_LOSS_EVENT_TYPE:
                    header = _DAILY_LOSS_REMINDER_HEADER
                    body = format_daily_loss_notification(event)
                    buttons = _daily_loss_buttons(event.event_id)
                else:
                    header = _REMINDER_HEADER
                    body = format_execution_notification(event)
                    buttons = _handled_buttons(event.event_id)
                await self._client.send_message(
                    self._allowlisted_user_id, f"{header}\n\n{body}", buttons=buttons
                )
                consumer = OutboxConsumer(self._session_factory, _EXECUTION_REMINDER_CONSUMER_NAME)
                consumer.acknowledge(event.event_id)
                count += 1
        return count

    async def _poll_execution_events_forever(self) -> None:
        attempt = 0
        while True:
            await asyncio.sleep(self._execution_poll_interval_seconds)
            try:
                await self.notify_execution_events()
                await self.remind_unhandled_failures()
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
        new_value: Decimal | list[Decimal] | EntryEdit
        try:
            if pending.field == "ENTRY":
                new_value = parse_entry_input(text)
            elif pending.field == "SIZE":
                new_value = parse_size_input(text)
            else:
                value = Decimal(text.strip())
                if not value.is_finite() or value <= 0:
                    raise InvalidOperation("value must be positive")
                new_value = value if pending.field == "STOP" else [value]
        except DraftEditError as error:
            await event.reply(f"⚠️ {error}，或重新點擊編輯按鈕。")
            return
        except InvalidOperation:
            await event.reply("⚠️ 格式錯誤，請輸入正數數字，或重新點擊編輯按鈕。")
            return

        preview: RiskEvaluation | None = None
        with self._session_factory.begin() as session:
            request = session.get(SignalDecisionRequest, pending.request_id)
            if request is None:
                signal = None
                draft = None
                nonce = None
            else:
                draft = create_edit(session, request, field=pending.field, value=new_value)
                signal = session.get(NormalizedSignal, request.signal_row_id)
                nonce = request.nonce
            raw_message = (
                session.get(TelegramMessageVersion, signal.raw_message_id)
                if signal is not None
                else None
            )
            if (
                signal is not None
                and raw_message is not None
                and draft is not None
                and self._risk_config is not None
                and self._market_data_client is not None
            ):
                try:
                    preview = preview_trade_intent(
                        signal,
                        raw_message,
                        config=self._risk_config,
                        market_data_client=self._market_data_client,
                        now=self._clock(),
                        daily_realized_loss_pct=daily_loss_pct(
                            session, self._clock(), self._risk_config.equity_baseline_usdt
                        ),
                        draft=draft,
                    )
                except Exception:
                    LOGGER.exception(
                        "preview after edit failed unexpectedly; showing the draft without one",
                        extra={"context": {"request_id": pending.request_id}},
                    )

        if signal is None or draft is None or nonce is None:
            await event.reply("⚠️ 找不到這個請求，可能已經過期或被清除，請重新查看 /signals。")
            return

        label = _EDIT_FIELD_LABELS[pending.field]
        await event.reply(
            f"✅ 已更新{label}草稿。\n\n"
            + format_signal_notification(
                signal,
                draft=draft,
                preview=preview,
                leverage=self._risk_config.leverage if self._risk_config else None,
                default_size_pct=(
                    self._risk_config.position_size_pct if self._risk_config else None
                ),
            ),
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
        daily_loss = decode_daily_loss_callback(data)
        if daily_loss is not None:
            await self._decide_daily_loss(event, *daily_loss)
            return

        handled_prefix = decode_handled_callback(data)
        if handled_prefix is not None:
            await self._mark_failure_handled(event, handled_prefix)
            return

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
                hint = "（純數字）"
            elif field == "TAKE_PROFIT":
                current_value = ", ".join(_format_decimal(tp) for tp in draft.take_profits) or None
                hint = "（純數字）"
            elif field == "ENTRY":
                if draft.entry_type == "MARKET":
                    current_value = "市價"
                elif draft.entry_value is not None:
                    current_value = f"限價 {_format_decimal(draft.entry_value)}"
                else:
                    current_value = None
                hint = "（輸入價格 = 限價單；輸入「市價」= 市價單）"
            else:
                current_value = (
                    f"{_format_decimal(draft.position_size_pct * 100)}%"
                    if draft.position_size_pct is not None
                    else None
                )
                hint = "（資金拿來當保證金的百分比，例如 10 代表 10%，上限 100）"
            await event.answer()
            await event.reply(
                f"目前{label}為 {current_value if current_value is not None else '預設'}。"
                f"請在 {_EDIT_REPLY_WINDOW_SECONDS} 秒內回覆新的{label}{hint}。"
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

    async def _decide_daily_loss(self, event: Any, event_id_prefix: str, decision: str) -> None:
        with self._session_factory.begin() as session:
            alert = find_event_by_id_prefix(session, event_id_prefix)
            if alert is None or alert.event_type != DAILY_LOSS_EVENT_TYPE:
                await event.answer("找不到這則通知", alert=True)
                return
            result = record_pause_decision(
                session,
                alert,
                decision=decision,
                actor_user_id=event.sender_id,
                now=self._clock(),
            )
            text = format_daily_loss_notification(alert)
            alert_event_id = alert.event_id
        # Counts as handled, so the 5-minute reminder is not sent after an answer.
        OutboxConsumer(self._session_factory, _EXECUTION_HANDLED_CONSUMER_NAME).acknowledge(
            alert_event_id
        )
        if result.decision == "STOP" and result.paused_until is not None:
            outcome = f"⛔ 已停止今日開單，到台灣時間 {_taiwan_time(result.paused_until)} 恢復"
        else:
            outcome = "▶️ 已選擇繼續開單"
        await event.edit(f"{text}\n\n{outcome}", buttons=None)
        await event.answer(f"之前已經選過了：{outcome}" if result.duplicate else outcome)

    async def _mark_failure_handled(self, event: Any, event_id_prefix: str) -> None:
        with self._session_factory() as session:
            outbox_event = find_event_by_id_prefix(session, event_id_prefix)
        if outbox_event is None:
            await event.answer("找不到這則通知", alert=True)
            return
        result = OutboxConsumer(
            self._session_factory, _EXECUTION_HANDLED_CONSUMER_NAME
        ).acknowledge(outbox_event.event_id)
        await event.edit(
            f"{format_execution_notification(outbox_event)}\n\n{_HANDLED_FOOTER}", buttons=None
        )
        await event.answer("之前已經標記過了" if result.duplicate else "已標記為已處理，不會再提醒")

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
