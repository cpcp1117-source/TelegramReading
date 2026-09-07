from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.tl.types import PeerChannel  # type: ignore[import-untyped]

from telegram_trader.config import Settings, TelegramChannelTarget


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


async def find_target_dialogs(
    client: ReadOnlyTelegramClient,
    targets: Sequence[TelegramChannelTarget],
) -> list[ChannelDialogSummary]:
    """Confirm membership for configured targets by numeric channel_id.

    Matches by channel_id (not username) so it works for both public and
    private targets. Dialogs that are not one of the configured targets are
    never included, so unrelated channel names stay private.
    """
    target_channel_ids = {target.channel_id for target in targets}
    dialogs: dict[int, ChannelDialogSummary] = {}
    async for dialog in client.iter_dialogs():
        if not dialog.is_channel or dialog.entity.id not in target_channel_ids:
            continue
        dialogs[dialog.entity.id] = ChannelDialogSummary(
            channel_id=dialog.entity.id,
            title=dialog.entity.title or "",
            username=dialog.entity.username,
            is_target=True,
        )
    return sorted(dialogs.values(), key=lambda item: item.channel_id)


async def list_private_channel_dialogs(
    client: ReadOnlyTelegramClient,
) -> list[ChannelDialogSummary]:
    """List already-joined channels that have no public username.

    Used only for onboarding discovery: a private channel has no `@username`,
    so a target for it can't be resolved until its numeric channel_id is known.
    The caller must have already joined the channel through the official
    Telegram client.
    """
    dialogs: list[ChannelDialogSummary] = []
    async for dialog in client.iter_dialogs():
        if not dialog.is_channel or dialog.entity.username:
            continue
        dialogs.append(
            ChannelDialogSummary(
                channel_id=dialog.entity.id,
                title=dialog.entity.title or "",
                username=None,
                is_target=False,
            )
        )
    return sorted(dialogs, key=lambda item: item.channel_id)


async def resolve_channel_entities(
    client: Any,
    targets: Sequence[TelegramChannelTarget],
) -> dict[int, Any]:
    """Resolve one entity per distinct channel_id, by username or numeric id.

    `client` is the live Telethon client rather than `ReadOnlyTelegramClient`
    because `get_dialogs`/`get_entity` aren't part of that narrow protocol.
    """
    await client.get_dialogs()
    entities: dict[int, Any] = {}
    for target in targets:
        if target.channel_id in entities:
            continue
        if target.username:
            entity = await client.get_entity(target.username)
        else:
            entity = await client.get_entity(PeerChannel(target.channel_id))
        if int(entity.id) != target.channel_id:
            raise ValueError(
                f"resolved Telegram entity does not match allowlisted channel ID "
                f"{target.channel_id}"
            )
        entities[target.channel_id] = entity
    return entities


async def preview_recent_messages(
    client: Any,
    entity: Any,
    topic_id: int | None,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Read-only message preview for onboarding fixture review.

    Never persists anything and never downloads media — only reports whether
    a message has a photo, so a chart-heavy topic can still be reviewed
    without touching the production database or media store.
    """
    results: list[dict[str, object]] = []
    async for message in client.iter_messages(entity, limit=limit, reply_to=topic_id):
        raw_text = getattr(message, "message", None)
        results.append(
            {
                "message_id": int(message.id),
                "date": message.date.isoformat(),
                "has_photo": getattr(message, "photo", None) is not None,
                "text": str(raw_text) if raw_text is not None else None,
            }
        )
    return results


def public_dict(value: AccountSummary | ChannelDialogSummary) -> dict[str, object]:
    return asdict(value)
