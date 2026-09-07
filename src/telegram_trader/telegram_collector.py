from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from telethon import events  # type: ignore[import-untyped]
from telethon.tl.types import PeerChannel  # type: ignore[import-untyped]

from telegram_trader.config import TelegramChannelTarget, get_settings
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
    def checkpoint(self, channel_id: int, topic_id: int | None = None) -> int: ...

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


def _topic_id(message: Any) -> int | None:
    """A forum-topic id, or None for a plain channel/non-topic message.

    Telegram's own quirk: a message that replies directly to a topic's root
    only sets `reply_to_msg_id` (the root id), not `reply_to_top_id`.
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None or not getattr(reply_to, "forum_topic", False):
        return None
    top_id = getattr(reply_to, "reply_to_top_id", None)
    if top_id is not None:
        return int(top_id)
    msg_id = getattr(reply_to, "reply_to_msg_id", None)
    return int(msg_id) if msg_id is not None else None


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
        topic_id=_topic_id(message),
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
        targets: Sequence[TelegramChannelTarget],
        initial_backfill_limit: int = 500,
        backfill_overlap: int = 100,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not targets:
            raise ValueError("at least one target is required")
        self._client = client
        self._sink = sink
        self._targets = list(targets)
        self._initial_backfill_limit = initial_backfill_limit
        self._backfill_overlap = backfill_overlap
        self._clock = clock or (lambda: datetime.now(UTC))

    async def resolve_targets(self) -> dict[int, Any]:
        """Resolve one entity per distinct channel_id across all configured targets."""
        await self._client.get_dialogs()
        entities: dict[int, Any] = {}
        for target in self._targets:
            if target.channel_id in entities:
                continue
            if target.username:
                entity = await self._client.get_entity(target.username)
            else:
                entity = await self._client.get_entity(PeerChannel(target.channel_id))
            if int(entity.id) != target.channel_id:
                raise ValueError(
                    f"resolved Telegram entity does not match allowlisted channel ID "
                    f"{target.channel_id}"
                )
            entities[target.channel_id] = entity
        return entities

    def _matching_target(
        self, channel_id: int, topic_id: int | None
    ) -> TelegramChannelTarget | None:
        return next(
            (
                target
                for target in self._targets
                if target.channel_id == channel_id and target.topic_id == topic_id
            ),
            None,
        )

    async def persist(self, message: Any, event_kind: TelegramEventKind) -> object | None:
        prepared = await telegram_message_input(self._client, message, event_kind, self._clock())
        if self._matching_target(prepared.channel_id, prepared.topic_id) is None:
            return None
        return self._sink.process(prepared)

    async def backfill(self, target: TelegramChannelTarget, entity: Any) -> int:
        checkpoint = self._sink.checkpoint(target.channel_id, target.topic_id)
        min_id = max(0, checkpoint - self._backfill_overlap) if checkpoint else 0
        messages = [
            message
            async for message in self._client.iter_messages(
                entity,
                min_id=min_id,
                limit=self._initial_backfill_limit,
                reply_to=target.topic_id,
            )
        ]
        for message in sorted(messages, key=lambda item: int(item.id)):
            await self.persist(message, "BACKFILL")
        return len(messages)

    async def run_connection(self) -> None:
        await self._client.start()
        entities = await self.resolve_targets()

        async def on_new(event: Any) -> None:
            await self.persist(event.message, "NEW")

        async def on_edit(event: Any) -> None:
            await self.persist(event.message, "EDITED")

        chats = list(entities.values())
        new_builder = events.NewMessage(chats=chats)
        edit_builder = events.MessageEdited(chats=chats)
        self._client.add_event_handler(on_new, new_builder)
        self._client.add_event_handler(on_edit, edit_builder)
        try:
            for target in self._targets:
                backfilled = await self.backfill(target, entities[target.channel_id])
                LOGGER.info(
                    "telegram collector ready",
                    extra={
                        "context": {
                            "channel_id": target.channel_id,
                            "topic_id": target.topic_id,
                            "label": target.label,
                            "backfill_seen": backfilled,
                            "checkpoint": self._sink.checkpoint(target.channel_id, target.topic_id),
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
        frozenset(target.identity for target in settings.telegram_target_channels),
    )
    collector = TelethonReadOnlyCollector(
        client,
        sink,
        targets=settings.telegram_target_channels,
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
