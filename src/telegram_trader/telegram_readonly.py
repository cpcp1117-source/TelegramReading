from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, TypeVar, cast

from telethon import TelegramClient  # type: ignore[import-untyped]

from telegram_trader.config import Settings


class TelegramEntity(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def title(self) -> str | None: ...

    @property
    def username(self) -> str | None: ...


class TelegramDialog(Protocol):
    @property
    def entity(self) -> TelegramEntity: ...

    @property
    def is_channel(self) -> bool: ...


class TelegramAccount(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def username(self) -> str | None: ...


class ReadOnlyTelegramClient(Protocol):
    async def start(self) -> object: ...

    async def disconnect(self) -> None: ...

    def iter_dialogs(self) -> AsyncIterator[TelegramDialog]: ...

    async def get_me(self) -> TelegramAccount: ...


ClientT = TypeVar("ClientT", bound=ReadOnlyTelegramClient)
ClientFactory = Callable[[str, int, str], ReadOnlyTelegramClient]


@dataclass(frozen=True, slots=True)
class AccountSummary:
    account_id: int
    username: str | None


@dataclass(frozen=True, slots=True)
class ChannelDialogSummary:
    channel_id: int
    title: str
    username: str | None
    is_target: bool


def prepare_session_path(session_path: Path) -> Path:
    """Create only the ignored parent directory; never create or read a session here."""
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return session_path


def create_client(
    settings: Settings,
    client_factory: ClientFactory | None = None,
) -> ReadOnlyTelegramClient:
    """Build a client without connecting or exposing credential values."""
    if settings.environment != "telegram_readonly":
        raise ValueError("Telegram client is available only in telegram_readonly mode")
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        raise ValueError("Telegram credentials are unavailable")

    session_path = prepare_session_path(settings.telegram_session_path)
    api_hash = settings.telegram_api_hash.get_secret_value()
    factory = client_factory or cast(ClientFactory, TelegramClient)
    return factory(str(session_path), settings.telegram_api_id, api_hash)


async def account_summary(client: ReadOnlyTelegramClient) -> AccountSummary:
    """Return a deliberately minimal account view; phone numbers are never returned."""
    account = await client.get_me()
    return AccountSummary(account_id=account.id, username=account.username)


async def list_channel_dialogs(
    client: ReadOnlyTelegramClient,
    target_username: str,
) -> list[ChannelDialogSummary]:
    """List channel dialogs deterministically without message content."""
    normalized_target = target_username.removeprefix("@").lower()
    dialogs: list[ChannelDialogSummary] = []
    async for dialog in client.iter_dialogs():
        if not dialog.is_channel:
            continue
        username = dialog.entity.username
        normalized_username = username.lower() if username else None
        dialogs.append(
            ChannelDialogSummary(
                channel_id=dialog.entity.id,
                title=dialog.entity.title or "",
                username=username,
                is_target=normalized_username == normalized_target,
            )
        )
    return sorted(dialogs, key=lambda item: (not item.is_target, item.channel_id))


def public_dict(value: AccountSummary | ChannelDialogSummary) -> dict[str, object]:
    return asdict(value)
