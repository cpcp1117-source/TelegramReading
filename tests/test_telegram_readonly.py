from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from telethon.tl.types import PeerChannel  # type: ignore[import-untyped]

from telegram_trader.config import Settings, TelegramChannelTarget
from telegram_trader.telegram_cli import target_dialog_listing_result
from telegram_trader.telegram_readonly import (
    ChannelDialogSummary,
    TelegramDialog,
    account_summary,
    create_client,
    find_target_dialogs,
    list_private_channel_dialogs,
    prepare_session_path,
    preview_recent_messages,
    public_dict,
    resolve_channel_entities,
)

NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)


@dataclass
class FakeEntity:
    id: int
    title: str | None = None
    username: str | None = None


@dataclass
class FakeDialog:
    entity: FakeEntity
    is_channel: bool


@dataclass
class FakeAccount:
    id: int
    username: str | None
    phone: str


@dataclass
class FakeMessage:
    id: int
    message: str | None = "message"
    date: datetime = field(default_factory=lambda: NOW)
    photo: object | None = None


class FakeClient:
    def __init__(
        self,
        dialogs: Sequence[TelegramDialog] | None = None,
        messages: Sequence[FakeMessage] | None = None,
    ) -> None:
        self.dialogs = list(dialogs or [])
        self.messages = list(messages or [])
        self.started = False
        self.disconnected = False
        self.dialogs_fetched = False
        self.iter_arguments: dict[str, object] = {}

    async def start(self) -> object:
        self.started = True
        return self

    async def disconnect(self) -> None:
        self.disconnected = True

    async def get_me(self) -> FakeAccount:
        return FakeAccount(id=42, username="collector", phone="+000000000")

    async def iter_dialogs(self) -> AsyncIterator[TelegramDialog]:
        for dialog in self.dialogs:
            yield dialog

    async def get_dialogs(self) -> list[object]:
        self.dialogs_fetched = True
        return []

    async def get_entity(self, target: object) -> FakeEntity:
        if isinstance(target, PeerChannel):
            return FakeEntity(target.channel_id)
        assert isinstance(target, str)
        return FakeEntity(10, username=target)

    async def iter_messages(self, entity: object, **kwargs: object) -> AsyncIterator[FakeMessage]:
        self.iter_arguments = kwargs
        for message in self.messages:
            yield message


def telegram_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="telegram_readonly",
        telegram_api_id=12345,
        telegram_api_hash=SecretStr("placeholder"),
        telegram_session_path=tmp_path / "collector",
    )


def test_prepare_session_path_creates_only_parent(tmp_path: Path) -> None:
    session_path = tmp_path / "private" / "collector"

    assert prepare_session_path(session_path) == session_path
    assert session_path.parent.is_dir()
    assert not session_path.exists()


def test_create_client_does_not_connect_and_passes_credentials(tmp_path: Path) -> None:
    captured: tuple[str, int, str] | None = None
    fake = FakeClient()

    def factory(session: str, api_id_value: int, credential_value: str) -> FakeClient:
        nonlocal captured
        captured = (session, api_id_value, credential_value)
        return fake

    client = create_client(telegram_settings(tmp_path), factory)

    assert client is fake
    assert captured == (str(tmp_path / "collector"), 12345, "placeholder")
    assert not fake.started
    assert not (tmp_path / "collector").exists()


def test_create_client_fails_closed_in_offline_mode() -> None:
    with pytest.raises(ValueError, match="telegram_readonly"):
        create_client(Settings())


@pytest.mark.anyio
async def test_account_summary_excludes_phone_number() -> None:
    summary = await account_summary(FakeClient())

    assert public_dict(summary) == {"account_id": 42, "username": "collector"}
    assert "phone" not in public_dict(summary)


@pytest.mark.anyio
async def test_find_target_dialogs_matches_by_channel_id_only() -> None:
    client = FakeClient(
        [
            FakeDialog(FakeEntity(30, "Unrelated", "other"), is_channel=True),
            FakeDialog(FakeEntity(20, "Private Chat", None), is_channel=False),
            FakeDialog(FakeEntity(10, "Monster", "followgerry"), is_channel=True),
            FakeDialog(FakeEntity(2382278102, "Bonnie Group", None), is_channel=True),
        ]
    )
    targets = [
        TelegramChannelTarget(channel_id=10, topic_id=None, username="followgerry", label="A"),
        TelegramChannelTarget(channel_id=2382278102, topic_id=21, username=None, label="B"),
        TelegramChannelTarget(channel_id=999, topic_id=None, username=None, label="C"),
    ]

    dialogs = await find_target_dialogs(client, targets)

    assert [dialog.channel_id for dialog in dialogs] == [10, 2382278102]
    assert all(dialog.is_target for dialog in dialogs)
    assert not any(dialog.channel_id == 30 for dialog in dialogs)


@pytest.mark.anyio
async def test_private_channel_discovery_lists_only_channels_without_username() -> None:
    client = FakeClient(
        [
            FakeDialog(FakeEntity(30, "Public Other", "other"), is_channel=True),
            FakeDialog(FakeEntity(20, "Private Chat", None), is_channel=False),
            FakeDialog(FakeEntity(10, "Secret Signals", None), is_channel=True),
        ]
    )

    dialogs = await list_private_channel_dialogs(client)

    assert dialogs == [ChannelDialogSummary(10, "Secret Signals", None, False)]


@pytest.mark.anyio
async def test_resolve_channel_entities_supports_username_and_numeric_id() -> None:
    client = FakeClient()
    targets = [
        TelegramChannelTarget(channel_id=10, topic_id=None, username="followgerry", label="A"),
        TelegramChannelTarget(channel_id=2382278102, topic_id=21, username=None, label="B"),
    ]

    entities = await resolve_channel_entities(client, targets)

    assert client.dialogs_fetched is True
    assert entities[10].id == 10
    assert entities[2382278102].id == 2382278102


@pytest.mark.anyio
async def test_resolve_channel_entities_fails_closed_on_mismatch() -> None:
    client = FakeClient()

    async def wrong_entity(_target: object) -> FakeEntity:
        return FakeEntity(1)

    client.get_entity = wrong_entity  # type: ignore[assignment]
    targets = [
        TelegramChannelTarget(channel_id=10, topic_id=None, username="followgerry", label="A")
    ]

    with pytest.raises(ValueError, match="does not match"):
        await resolve_channel_entities(client, targets)


@pytest.mark.anyio
async def test_preview_recent_messages_reports_photo_presence_without_downloading() -> None:
    client = FakeClient(
        messages=[
            FakeMessage(id=1, message="text only", photo=None),
            FakeMessage(id=2, message="chart caption", photo=object()),
        ]
    )

    preview = await preview_recent_messages(client, FakeEntity(10), topic_id=21, limit=20)

    assert client.iter_arguments == {"limit": 20, "reply_to": 21}
    assert preview[0]["has_photo"] is False
    assert preview[1]["has_photo"] is True
    assert preview[1]["text"] == "chart caption"
    assert not hasattr(client, "download_media")


def test_target_dialog_result_does_not_expose_unrelated_channel_details() -> None:
    targets = [
        TelegramChannelTarget(channel_id=10, topic_id=None, username="followgerry", label="A"),
        TelegramChannelTarget(channel_id=999, topic_id=None, username=None, label="Missing"),
    ]
    found_dialogs = [ChannelDialogSummary(10, "Monster", "followgerry", True)]

    result = target_dialog_listing_result(targets, found_dialogs)

    assert result["targets"] == [
        {"label": "A", "channel_id": 10, "topic_id": None, "found": True, "title": "Monster"},
        {"label": "Missing", "channel_id": 999, "topic_id": None, "found": False, "title": None},
    ]
    assert "Private Other" not in str(result)
