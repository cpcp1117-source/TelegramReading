from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from telegram_trader.telegram_collector import (
    TelethonReadOnlyCollector,
    reconnect_delay,
    telegram_message_input,
)
from telegram_trader.telegram_storage import TelegramMessageInput

NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)


@dataclass
class FakePeer:
    channel_id: int


@dataclass
class FakeFile:
    name: str | None = None
    ext: str | None = ".jpg"
    mime_type: str | None = "image/jpeg"


@dataclass
class FakeForward:
    from_id: object | None
    from_name: str | None
    channel_post: int | None
    date: datetime


@dataclass
class FakeMessage:
    id: int
    message: str | None = "message"
    peer_id: object = field(default_factory=lambda: FakePeer(2439599598))
    date: datetime = NOW
    edit_date: datetime | None = None
    reply_to_msg_id: int | None = None
    fwd_from: FakeForward | None = None
    photo: object | None = None
    file: FakeFile | None = None


@dataclass
class FakeEntity:
    id: int


class FakeClient:
    def __init__(self, messages: list[FakeMessage] | None = None) -> None:
        self.messages = messages or []
        self.iter_arguments: dict[str, object] = {}
        self.handlers: list[tuple[object, object]] = []
        self.started = False
        self.disconnected = False
        self.raise_on_run = False

    async def download_media(self, message: object, *, file: type[bytes]) -> bytes:
        assert file is bytes
        return b"synthetic-image"

    async def get_entity(self, username: str) -> FakeEntity:
        assert username == "followgerry"
        return FakeEntity(2439599598)

    async def iter_messages(self, entity: object, **kwargs: object) -> AsyncIterator[FakeMessage]:
        self.iter_arguments = kwargs
        for message in self.messages:
            yield message

    async def start(self) -> object:
        self.started = True
        return self

    def add_event_handler(self, callback: object, builder: object) -> None:
        self.handlers.append((callback, builder))

    def remove_event_handler(self, callback: object, builder: object) -> None:
        self.handlers.remove((callback, builder))

    async def run_until_disconnected(self) -> None:
        if self.raise_on_run:
            raise ConnectionError("synthetic disconnect")


class FakeSink:
    def __init__(self, checkpoint: int = 0) -> None:
        self.value = checkpoint
        self.messages: list[TelegramMessageInput] = []

    def checkpoint(self) -> int:
        return self.value

    def process(self, message: TelegramMessageInput) -> object:
        self.messages.append(message)
        self.value = max(self.value, message.message_id)
        return message


@pytest.mark.anyio
async def test_message_conversion_preserves_caption_image_reply_and_forward() -> None:
    forward = FakeForward(FakePeer(777), None, 88, NOW)
    message = FakeMessage(
        id=123,
        message="chart caption",
        reply_to_msg_id=120,
        fwd_from=forward,
        photo=object(),
        file=FakeFile(name="chart.jpg"),
    )

    prepared = await telegram_message_input(FakeClient(), message, "NEW", NOW)

    assert prepared.channel_id == 2439599598
    assert prepared.content_type == "caption"
    assert prepared.media_bytes == b"synthetic-image"
    assert prepared.media_filename == "chart.jpg"
    assert prepared.reply_to_message_id == 120
    assert prepared.forward_origin_type == "channel"
    assert prepared.forward_origin_id == 777
    assert prepared.forward_message_id == 88


@pytest.mark.anyio
async def test_backfill_uses_overlap_and_persists_in_message_order() -> None:
    client = FakeClient([FakeMessage(105), FakeMessage(101), FakeMessage(103)])
    sink = FakeSink(checkpoint=100)
    collector = TelethonReadOnlyCollector(
        client,
        sink,
        target_username="followgerry",
        expected_channel_id=2439599598,
        initial_backfill_limit=50,
        backfill_overlap=10,
        clock=lambda: NOW,
    )

    count = await collector.backfill(FakeEntity(2439599598))

    assert count == 3
    assert client.iter_arguments == {"min_id": 90, "limit": 50}
    assert [message.message_id for message in sink.messages] == [101, 103, 105]
    assert all(message.event_kind == "BACKFILL" for message in sink.messages)


@pytest.mark.anyio
async def test_target_id_mismatch_fails_closed() -> None:
    client = FakeClient()

    async def wrong_entity(_username: str) -> FakeEntity:
        return FakeEntity(1)

    client.get_entity = wrong_entity  # type: ignore[assignment]
    collector = TelethonReadOnlyCollector(
        client,
        FakeSink(),
        target_username="followgerry",
        expected_channel_id=2439599598,
    )

    with pytest.raises(ValueError, match="does not match"):
        await collector.resolve_target()


@pytest.mark.anyio
async def test_connection_removes_handlers_after_disconnect_error() -> None:
    client = FakeClient()
    client.raise_on_run = True
    collector = TelethonReadOnlyCollector(
        client,
        FakeSink(),
        target_username="followgerry",
        expected_channel_id=2439599598,
        clock=lambda: NOW,
    )

    with pytest.raises(ConnectionError, match="synthetic"):
        await collector.run_connection()

    assert client.started is True
    assert client.handlers == []


def test_reconnect_delay_is_exponential_and_capped() -> None:
    assert [reconnect_delay(attempt) for attempt in range(4)] == [1, 2, 4, 8]
    assert reconnect_delay(20) == 60
    with pytest.raises(ValueError, match="negative"):
        reconnect_delay(-1)
