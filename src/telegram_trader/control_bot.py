from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy.orm import Session, sessionmaker
from telethon import Button, TelegramClient, events  # type: ignore[import-untyped]

from telegram_trader.config import Settings, get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.models import NormalizedSignal, SignalDecisionRequest
from telegram_trader.signal_decisions import (
    DecisionAction,
    DecisionOutcome,
    create_request,
    load_pending_signals,
    record_decision,
    request_id_for_signal_row,
)
from telegram_trader.telegram_collector import reconnect_delay
from telegram_trader.telegram_readonly import prepare_session_path

LOGGER = logging.getLogger(__name__)

_CALLBACK_SEP = ":"
_CALLBACK_PREFIX_SIGNAL = "signal"
_CALLBACK_PREFIX_CLOSE_ALL = "close_all"
_CLOSE_ALL_CONFIRM_WINDOW_SECONDS = 30

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


def encode_signal_callback(request_id: str, nonce: str, action: DecisionAction) -> bytes:
    parts = (_CALLBACK_PREFIX_SIGNAL, request_id, nonce, action)
    return _CALLBACK_SEP.join(parts).encode()


def decode_signal_callback(data: bytes) -> tuple[str, str, DecisionAction] | None:
    try:
        text = data.decode()
    except UnicodeDecodeError:
        return None
    parts = text.split(_CALLBACK_SEP)
    if len(parts) != 4 or parts[0] != _CALLBACK_PREFIX_SIGNAL:
        return None
    _, request_id, nonce, action = parts
    if action not in ("APPROVE", "REJECT"):
        return None
    return request_id, nonce, cast(DecisionAction, action)


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


def format_signal_notification(signal: NormalizedSignal) -> str:
    lines = [
        "📡 新訊號待核准" if signal.status == "NEW" else "📡 訊號待核准",
        f"symbol: {signal.symbol or '(未知)'}",
        f"side: {signal.side or '(未知)'}",
        f"status: {signal.status}",
        f"entry_type: {signal.entry_type or '-'}",
        f"stop_origin: {signal.stop_origin}",
    ]
    if signal.status == "NEW":
        lines.append(
            "⚠️ symbol 尚未經市場資料驗證（動態範圍頻道），此核准僅供記錄，不會有任何自動下單。"
        )
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


class ControlBot:
    def __init__(
        self,
        client: Any,
        session_factory: sessionmaker[Session],
        *,
        bot_token: str,
        allowlisted_user_id: int,
        poll_interval_seconds: float = 60.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._bot_token = bot_token
        self._allowlisted_user_id = allowlisted_user_id
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_poll_at: datetime | None = None
        self._pending_close_all_nonce: str | None = None
        self._pending_close_all_expires_at: datetime | None = None

    def _is_allowlisted(self, sender_id: int | None) -> bool:
        return sender_id is not None and sender_id == self._allowlisted_user_id

    async def notify_pending_signals(self) -> int:
        """Send one notification per pending signal, then record the request.

        A signal is only ever notified once: `load_pending_signals` excludes
        anything with an existing `signal_decision_requests` row, and that
        row is only created after the send below succeeds.
        """
        count = 0
        with self._session_factory() as session:
            pending = load_pending_signals(session)
            for signal in pending:
                nonce = generate_nonce()
                request_id = request_id_for_signal_row(signal.signal_row_id)
                buttons = [
                    [
                        Button.inline(
                            "✅ Approve", encode_signal_callback(request_id, nonce, "APPROVE")
                        ),
                        Button.inline(
                            "❌ Reject", encode_signal_callback(request_id, nonce, "REJECT")
                        ),
                    ]
                ]
                message = await self._client.send_message(
                    self._allowlisted_user_id,
                    format_signal_notification(signal),
                    buttons=buttons,
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

        decoded = decode_signal_callback(data)
        if decoded is None:
            await event.answer("無效的操作", alert=True)
            return
        request_id, nonce, action = decoded

        with self._session_factory.begin() as session:
            request = session.get(SignalDecisionRequest, request_id)
            if request is None or request.nonce != nonce:
                outcome = None
            else:
                outcome = record_decision(
                    session,
                    request_id=request_id,
                    actor_user_id=event.sender_id,
                    callback_query_id=str(event.query.id),
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
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task


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

    client = create_bot_client(settings)
    bot = ControlBot(
        client,
        session_factory,
        bot_token=settings.control_bot_token.get_secret_value(),
        allowlisted_user_id=settings.control_bot_allowlisted_user_id,
        poll_interval_seconds=settings.control_bot_poll_interval_seconds,
    )
    try:
        await bot.run_forever()
        return 0
    finally:
        await client.disconnect()
        engine.dispose()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
