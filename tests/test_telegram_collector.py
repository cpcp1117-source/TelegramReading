from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from telethon.tl.types import PeerChannel  # type: ignore[import-untyped]

from telegram_trader.config import TelegramChannelTarget
from telegram_trader.telegram_collector import (
    TelethonReadOnlyCollector,
    reconnect_delay,
    telegram_message_input,
)
from telegram_trader.telegram_storage import TelegramMessageInput

NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)

FOLLOWGERRY_TARGET = TelegramChannelTarget(
    channel_id=2439599598, topic_id=None, username="followgerry", label="Monster"
)
BTC_ETH_TOPIC_TARGET = TelegramChannelTarget(
    channel_id=2382278102, topic_id=21, username=None, label="Bonnie-BTC ETH"
)


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
class FakeReplyHeader:
    forum_topic: bool = False
    reply_to_top_id: int | None = None
    reply_to_msg_id: int | None = None


@dataclass
class FakeMessage:
    id: int
    message: str | None = "message"
    peer_id: object = field(default_factory=lambda: FakePeer(2439599598))
    date: datetime = NOW
    edit_date: datetime | None = None
    reply_to_msg_id: int | None = None
    reply_to: FakeReplyHeader | None = None
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
        self.dialogs_fetched = False

    async def download_media(self, message: object, *, file: type[bytes]) -> bytes:
        assert file is bytes
        return b"synthetic-image"

    async def get_dialogs(self) -> list[object]:
        self.dialogs_fetched = True
        return []

    async def get_entity(self, target: object) -> FakeEntity:
        if isinstance(target, PeerChannel):
            return FakeEntity(target.channel_id)
        assert target == "followgerry"
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
    def __init__(self, checkpoints: dict[tuple[int, int | None], int] | None = None) -> None:
        self.values: dict[tuple[int, int | None], int] = dict(checkpoints or {})
        self.messages: list[TelegramMessageInput] = []

    def checkpoint(self, channel_id: int, topic_id: int | None = None) -> int:
        return self.values.get((channel_id, topic_id), 0)

    def process(self, message: TelegramMessageInput) -> object:
        self.messages.append(message)
        key = (message.channel_id, message.topic_id)
        self.values[key] = max(self.values.get(key, 0), message.message_id)
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
    assert prepared.topic_id is None


@pytest.mark.anyio
async def test_message_conversion_extracts_topic_id_from_reply_to_top_id() -> None:
    message = FakeMessage(id=124, reply_to=FakeReplyHeader(forum_topic=True, reply_to_top_id=21))

    prepared = await telegram_message_input(FakeClient(), message, "NEW", NOW)

    assert prepared.topic_id == 21


@pytest.mark.anyio
async def test_message_conversion_falls_back_to_reply_to_msg_id_for_topic_root_reply() -> None:
    message = FakeMessage(
        id=125, reply_to=FakeReplyHeader(forum_topic=True, reply_to_top_id=None, reply_to_msg_id=21)
    )

    prepared = await telegram_message_input(FakeClient(), message, "NEW", NOW)

    assert prepared.topic_id == 21


@pytest.mark.anyio
async def test_message_conversion_ignores_non_forum_reply() -> None:
    message = FakeMessage(id=126, reply_to=FakeReplyHeader(forum_topic=False, reply_to_msg_id=99))

    prepared = await telegram_message_input(FakeClient(), message, "NEW", NOW)

    assert prepared.topic_id is None


@pytest.mark.anyio
async def test_backfill_uses_overlap_and_persists_in_message_order() -> None:
    client = FakeClient([FakeMessage(105), FakeMessage(101), FakeMessage(103)])
    sink = FakeSink(checkpoints={(2439599598, None): 100})
    collector = TelethonReadOnlyCollector(
        client,
        sink,
        targets=[FOLLOWGERRY_TARGET],
        initial_backfill_limit=50,
        backfill_overlap=10,
        clock=lambda: NOW,
    )

    count = await collector.backfill(FOLLOWGERRY_TARGET, FakeEntity(2439599598))

    assert count == 3
    assert client.iter_arguments == {"min_id": 90, "limit": 50, "reply_to": None}
    assert [message.message_id for message in sink.messages] == [101, 103, 105]
    assert all(message.event_kind == "BACKFILL" for message in sink.messages)


@pytest.mark.anyio
async def test_backfill_passes_topic_id_as_reply_to() -> None:
    client = FakeClient([FakeMessage(50, peer_id=FakePeer(2382278102))])
    sink = FakeSink()
    collector = TelethonReadOnlyCollector(client, sink, targets=[BTC_ETH_TOPIC_TARGET])

    await collector.backfill(BTC_ETH_TOPIC_TARGET, FakeEntity(2382278102))

    assert client.iter_arguments["reply_to"] == 21


@pytest.mark.anyio
async def test_resolve_targets_supports_username_and_numeric_id() -> None:
    client = FakeClient()
    collector = TelethonReadOnlyCollector(
        client, FakeSink(), targets=[FOLLOWGERRY_TARGET, BTC_ETH_TOPIC_TARGET]
    )

    entities = await collector.resolve_targets()

    assert client.dialogs_fetched is True
    assert entities[2439599598].id == 2439599598
    assert entities[2382278102].id == 2382278102


@pytest.mark.anyio
async def test_target_id_mismatch_fails_closed() -> None:
    client = FakeClient()

    async def wrong_entity(_target: object) -> FakeEntity:
        return FakeEntity(1)

    client.get_entity = wrong_entity  # type: ignore[assignment]
    collector = TelethonReadOnlyCollector(client, FakeSink(), targets=[FOLLOWGERRY_TARGET])

    with pytest.raises(ValueError, match="does not match"):
        await collector.resolve_targets()


@pytest.mark.anyio
async def test_connection_removes_handlers_after_disconnect_error() -> None:
    client = FakeClient()
    client.raise_on_run = True
    collector = TelethonReadOnlyCollector(
        client,
        FakeSink(),
        targets=[FOLLOWGERRY_TARGET],
        clock=lambda: NOW,
    )

    with pytest.raises(ConnectionError, match="synthetic"):
        await collector.run_connection()

    assert client.started is True
    assert client.handlers == []


def test_apply_policy_reload_shrinks_but_never_grows() -> None:
    collector = TelethonReadOnlyCollector(
        FakeClient(), FakeSink(), targets=[FOLLOWGERRY_TARGET, BTC_ETH_TOPIC_TARGET]
    )

    removed = collector._apply_policy_reload(frozenset({FOLLOWGERRY_TARGET.identity}))

    assert [target.label for target in removed] == [BTC_ETH_TOPIC_TARGET.label]
    assert collector._targets == [FOLLOWGERRY_TARGET]


def test_apply_policy_reload_ignores_targets_not_already_active() -> None:
    collector = TelethonReadOnlyCollector(FakeClient(), FakeSink(), targets=[FOLLOWGERRY_TARGET])

    removed = collector._apply_policy_reload(
        frozenset({FOLLOWGERRY_TARGET.identity, BTC_ETH_TOPIC_TARGET.identity})
    )

    assert removed == []
    assert collector._targets == [FOLLOWGERRY_TARGET]


def test_apply_policy_reload_no_change_returns_empty() -> None:
    collector = TelethonReadOnlyCollector(FakeClient(), FakeSink(), targets=[FOLLOWGERRY_TARGET])

    removed = collector._apply_policy_reload(frozenset({FOLLOWGERRY_TARGET.identity}))

    assert removed == []
    assert collector._targets == [FOLLOWGERRY_TARGET]


@pytest.mark.anyio
async def test_poll_policy_forever_retries_after_unexpected_error() -> None:
    collector = TelethonReadOnlyCollector(FakeClient(), FakeSink(), targets=[FOLLOWGERRY_TARGET])
    calls = 0

    async def flaky_reload(
        _session_factory: object, _full_targets: object
    ) -> list[TelegramChannelTarget]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated transient failure")
        return []

    collector.reload_policy_once = flaky_reload  # type: ignore[method-assign,assignment]
    task = asyncio.create_task(
        collector._poll_policy_forever(
            object(),  # type: ignore[arg-type]
            [FOLLOWGERRY_TARGET],
            interval_seconds=0.01,
        )
    )
    # reconnect_delay(0) == 1 second, so the retry after the first failure needs
    # over a second before the second call happens.
    for _ in range(200):
        if calls >= 2:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls >= 2


@pytest.mark.anyio
async def test_run_forever_cancels_policy_poll_task_on_shutdown() -> None:
    client = FakeClient()
    client.raise_on_run = True
    collector = TelethonReadOnlyCollector(client, FakeSink(), targets=[FOLLOWGERRY_TARGET])
    poll_calls = 0

    async def counting_reload(
        _session_factory: object, _full_targets: object
    ) -> list[TelegramChannelTarget]:
        nonlocal poll_calls
        poll_calls += 1
        return []

    collector.reload_policy_once = counting_reload  # type: ignore[method-assign,assignment]
    before = asyncio.all_tasks()
    task = asyncio.create_task(
        collector.run_forever(
            policy_session_factory=object(),  # type: ignore[arg-type]
            policy_full_targets=[FOLLOWGERRY_TARGET],
            policy_poll_interval_seconds=0.01,
        )
    )
    for _ in range(50):
        if poll_calls >= 1:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert poll_calls >= 1
    assert asyncio.all_tasks() - before == set()


def test_reconnect_delay_is_exponential_and_capped() -> None:
    assert [reconnect_delay(attempt) for attempt in range(4)] == [1, 2, 4, 8]
    assert reconnect_delay(20) == 60
    with pytest.raises(ValueError, match="negative"):
        reconnect_delay(-1)
