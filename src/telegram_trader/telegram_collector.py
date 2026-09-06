from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from telethon import events  # type: ignore[import-untyped]

from telegram_trader.config import get_settings
from telegram_trader.db import create_db_engine, create_session_factory
from telegram_trader.logging_config import configure_logging
from telegram_trader.telegram_readonly import create_client
from telegram_trader.telegram_storage import (
    ForwardOriginType,
    MediaStore,
    TelegramContentType,
    TelegramEventKind,
    TelegramMessageInput,
    TelegramMessageProcessor,
)

LOGGER = logging.getLogger(__name__)


class TelegramMessageSink(Protocol):
    def checkpoint(self) -> int: ...

    def process(self, message: TelegramMessageInput) -> object: ...


def reconnect_delay(attempt: int, cap_seconds: int = 60) -> int:
    if attempt < 0:
        raise ValueError("attempt cannot be negative")
    return int(min(pow(2, attempt), cap_seconds))


def _forward_origin(
    peer: object | None, from_name: str | None
) -> tuple[ForwardOriginType | None, int | None]:
    if peer is not None:
        for attribute, origin_type in (
            ("channel_id", "channel"),
            ("chat_id", "chat"),
            ("user_id", "user"),
        ):
            value = getattr(peer, attribute, None)
            if value is not None:
                return cast(ForwardOriginType, origin_type), int(value)
    if from_name:
        return "hidden", None
    return None, None


async def telegram_message_input(
    client: Any,
    message: Any,
    event_kind: TelegramEventKind,
    received_at: datetime,
) -> TelegramMessageInput:
    peer = getattr(message, "peer_id", None)
    channel_id = getattr(peer, "channel_id", None)
    if channel_id is None:
        raise ValueError("Telegram message is not from a channel")

    raw_text = getattr(message, "message", None)
    text = str(raw_text) if raw_text is not None else None
    photo = getattr(message, "photo", None)
    media_bytes: bytes | None = None
    media_filename: str | None = None
    media_mime_type: str | None = None
    if photo is not None:
        downloaded = await client.download_media(message, file=bytes)
        if not isinstance(downloaded, bytes) or not downloaded:
            raise ValueError("Telegram image download returned no bytes")
        media_bytes = downloaded
        file_metadata = getattr(message, "file", None)
        media_filename = getattr(file_metadata, "name", None)
        extension = getattr(file_metadata, "ext", None)
        if not media_filename:
            media_filename = f"image{extension or ''}"
        media_mime_type = getattr(file_metadata, "mime_type", None)

    content_type: TelegramContentType
    if photo is not None and text:
        content_type = "caption"
    elif photo is not None:
        content_type = "image"
    elif text:
        content_type = "text"
    else:
        content_type = "empty"

    forward = getattr(message, "fwd_from", None)
    forward_origin_type: ForwardOriginType | None = None
    forward_origin_id: int | None = None
    forward_message_id: int | None = None
    forward_date: datetime | None = None
    if forward is not None:
        forward_origin_type, forward_origin_id = _forward_origin(
            getattr(forward, "from_id", None), getattr(forward, "from_name", None)
        )
        forwarded_message = getattr(forward, "channel_post", None)
        forward_message_id = int(forwarded_message) if forwarded_message is not None else None
        forward_date = getattr(forward, "date", None)

    return TelegramMessageInput(
        channel_id=int(channel_id),
        message_id=int(message.id),
        event_kind=event_kind,
        source_date=message.date,
        received_at=received_at,
        text=text,
        content_type=content_type,
        edit_date=getattr(message, "edit_date", None),
        reply_to_message_id=getattr(message, "reply_to_msg_id", None),
        forward_origin_type=forward_origin_type,
        forward_origin_id=forward_origin_id,
        forward_message_id=forward_message_id,
        forward_date=forward_date,
        media_bytes=media_bytes,
        media_filename=media_filename,
        media_mime_type=media_mime_type,
    )


class TelethonReadOnlyCollector:
    def __init__(
        self,
        client: Any,
        sink: TelegramMessageSink,
        *,
        target_username: str,
        expected_channel_id: int,
        initial_backfill_limit: int = 500,
        backfill_overlap: int = 100,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._sink = sink
        self._target_username = target_username
        self._expected_channel_id = expected_channel_id
        self._initial_backfill_limit = initial_backfill_limit
        self._backfill_overlap = backfill_overlap
        self._clock = clock or (lambda: datetime.now(UTC))

    async def resolve_target(self) -> Any:
        entity = await self._client.get_entity(self._target_username)
        if int(entity.id) != self._expected_channel_id:
            raise ValueError("resolved Telegram entity does not match allowlisted channel ID")
        return entity

    async def persist(self, message: Any, event_kind: TelegramEventKind) -> object:
        prepared = await telegram_message_input(self._client, message, event_kind, self._clock())
        return self._sink.process(prepared)

    async def backfill(self, entity: Any) -> int:
        checkpoint = self._sink.checkpoint()
        min_id = max(0, checkpoint - self._backfill_overlap) if checkpoint else 0
        messages = [
            message
            async for message in self._client.iter_messages(
                entity,
                min_id=min_id,
                limit=self._initial_backfill_limit,
            )
        ]
        for message in sorted(messages, key=lambda item: int(item.id)):
            await self.persist(message, "BACKFILL")
        return len(messages)

    async def run_connection(self) -> None:
        await self._client.start()
        entity = await self.resolve_target()

        async def on_new(event: Any) -> None:
            await self.persist(event.message, "NEW")

        async def on_edit(event: Any) -> None:
            await self.persist(event.message, "EDITED")

        new_builder = events.NewMessage(chats=entity)
        edit_builder = events.MessageEdited(chats=entity)
        self._client.add_event_handler(on_new, new_builder)
        self._client.add_event_handler(on_edit, edit_builder)
        try:
            backfilled = await self.backfill(entity)
            LOGGER.info(
                "telegram collector ready",
                extra={
                    "context": {
                        "channel_id": self._expected_channel_id,
                        "backfill_seen": backfilled,
                        "checkpoint": self._sink.checkpoint(),
                    }
                },
            )
            await self._client.run_until_disconnected()
        finally:
            self._client.remove_event_handler(on_new, new_builder)
            self._client.remove_event_handler(on_edit, edit_builder)

    async def run_forever(self) -> None:
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
                    "telegram collector disconnected; retrying",
                    extra={
                        "context": {
                            "error_type": type(error).__name__,
                            "retry_seconds": delay,
                        }
                    },
                )
                attempt += 1
                await asyncio.sleep(delay)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2 Telegram read-only collector")
    parser.add_argument("--once", action="store_true", help="Stop after the first disconnect")
    return parser


async def _run(once: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    client = create_client(settings)
    sink = TelegramMessageProcessor(
        create_session_factory(engine),
        MediaStore(Path("media")),
        settings.telegram_target_channel_id,
    )
    collector = TelethonReadOnlyCollector(
        client,
        sink,
        target_username=settings.telegram_target_username,
        expected_channel_id=settings.telegram_target_channel_id,
    )
    try:
        if once:
            await collector.run_connection()
        else:
            await collector.run_forever()
        return 0
    finally:
        await client.disconnect()
        engine.dispose()


def main() -> int:
    args = _parser().parse_args()
    return asyncio.run(_run(args.once))


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
