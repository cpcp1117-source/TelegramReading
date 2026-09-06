from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr

from telegram_trader.config import Settings
from telegram_trader.telegram_readonly import (
    TelegramDialog,
    account_summary,
    create_client,
    list_channel_dialogs,
    prepare_session_path,
    public_dict,
)


@dataclass
class FakeEntity:
    id: int
    title: str | None
    username: str | None


@dataclass
class FakeDialog:
    entity: FakeEntity
    is_channel: bool


@dataclass
class FakeAccount:
    id: int
    username: str | None
    phone: str


class FakeClient:
    def __init__(self, dialogs: Sequence[TelegramDialog] | None = None) -> None:
        self.dialogs = list(dialogs or [])
        self.started = False
        self.disconnected = False

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
async def test_channel_dialog_listing_filters_and_prioritizes_target() -> None:
    client = FakeClient(
        [
            FakeDialog(FakeEntity(30, "Other", "other"), is_channel=True),
            FakeDialog(FakeEntity(20, "Private Chat", None), is_channel=False),
            FakeDialog(FakeEntity(10, "Monster", "FollowGerry"), is_channel=True),
        ]
    )

    dialogs = await list_channel_dialogs(client, "followgerry")

    assert [dialog.channel_id for dialog in dialogs] == [10, 30]
    assert dialogs[0].is_target is True
    assert dialogs[1].is_target is False
