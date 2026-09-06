from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.audit import append_audit_event
from telegram_trader.models import TelegramCollectorCheckpoint, TelegramMessageVersion
from telegram_trader.outbox import append_outbox_event

TelegramEventKind = Literal["NEW", "EDITED", "BACKFILL"]
TelegramContentType = Literal["text", "caption", "image", "empty"]
ForwardOriginType = Literal["channel", "chat", "user", "hidden"]


@dataclass(frozen=True, slots=True)
class TelegramMessageInput:
    channel_id: int
    message_id: int
    event_kind: TelegramEventKind
    source_date: datetime
    received_at: datetime
    text: str | None = None
    content_type: TelegramContentType = "empty"
    edit_date: datetime | None = None
    reply_to_message_id: int | None = None
    forward_origin_type: ForwardOriginType | None = None
    forward_origin_id: int | None = None
    forward_message_id: int | None = None
    forward_date: datetime | None = None
    media_bytes: bytes | None = None
    media_filename: str | None = None
    media_mime_type: str | None = None

    def __post_init__(self) -> None:
        if self.channel_id <= 0 or self.message_id <= 0:
            raise ValueError("Telegram channel_id and message_id must be positive")
        if self.reply_to_message_id is not None and self.reply_to_message_id <= 0:
            raise ValueError("reply_to_message_id must be positive")
        if self.forward_origin_id is not None and self.forward_origin_id <= 0:
            raise ValueError("forward_origin_id must be positive")
        if self.forward_message_id is not None and self.forward_message_id <= 0:
            raise ValueError("forward_message_id must be positive")
        if self.media_bytes == b"":
            raise ValueError("media_bytes cannot be empty")
        for value in (self.source_date, self.received_at, self.edit_date, self.forward_date):
            if value is not None and value.tzinfo is None:
                raise ValueError("Telegram timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class StoredMedia:
    sha256: str
    relative_path: str
    size_bytes: int


class MediaStore:
    _ALLOWED_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})

    def __init__(self, root: Path) -> None:
        self._root = root

    def store(
        self,
        *,
        channel_id: int,
        message_id: int,
        edit_version: int,
        content: bytes,
        filename: str | None,
    ) -> StoredMedia:
        digest = hashlib.sha256(content).hexdigest()
        extension = Path(filename or "").suffix.lower()
        if extension not in self._ALLOWED_EXTENSIONS:
            extension = ".bin"
        relative = Path(str(channel_id)) / str(message_id) / f"{edit_version}-{digest}{extension}"
        destination = self._root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            handle, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=".incoming-")
            try:
                with os.fdopen(handle, "wb") as temporary:
                    temporary.write(content)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, destination)
            finally:
                temporary_path = Path(temporary_name)
                if temporary_path.exists():
                    temporary_path.unlink()
        return StoredMedia(digest, relative.as_posix(), len(content))


@dataclass(frozen=True, slots=True)
class TelegramPersistResult:
    source_event_id: str
    audit_event_id: str
    outbox_event_id: str
    edit_version: int
    checkpoint: int
    duplicate: bool
    media_sha256: str | None


def content_fingerprint(message: TelegramMessageInput) -> tuple[str, str | None]:
    media_sha256 = (
        hashlib.sha256(message.media_bytes).hexdigest() if message.media_bytes is not None else None
    )
    canonical = {
        "channel_id": message.channel_id,
        "message_id": message.message_id,
        "source_date": message.source_date.isoformat(),
        "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        "text": message.text,
        "content_type": message.content_type,
        "reply_to_message_id": message.reply_to_message_id,
        "forward_origin_type": message.forward_origin_type,
        "forward_origin_id": message.forward_origin_id,
        "forward_message_id": message.forward_message_id,
        "forward_date": message.forward_date.isoformat() if message.forward_date else None,
        "media_sha256": media_sha256,
        "media_mime_type": message.media_mime_type,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), media_sha256


def _stable_id(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()


class TelegramMessageProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        media_store: MediaStore,
        expected_channel_id: int,
    ) -> None:
        if expected_channel_id <= 0:
            raise ValueError("expected_channel_id must be positive")
        self._session_factory = session_factory
        self._media_store = media_store
        self._expected_channel_id = expected_channel_id

    def checkpoint(self) -> int:
        with self._session_factory() as session:
            checkpoint = session.get(TelegramCollectorCheckpoint, self._expected_channel_id)
            return checkpoint.last_message_id if checkpoint is not None else 0

    def process(self, message: TelegramMessageInput) -> TelegramPersistResult:
        if message.channel_id != self._expected_channel_id:
            raise ValueError("message channel is not the Phase 2 allowlisted channel")
        content_hash, media_sha256 = content_fingerprint(message)

        with self._session_factory.begin() as session:
            versions = list(
                session.scalars(
                    select(TelegramMessageVersion)
                    .where(
                        TelegramMessageVersion.channel_id == message.channel_id,
                        TelegramMessageVersion.message_id == message.message_id,
                    )
                    .order_by(TelegramMessageVersion.edit_version.desc())
                    .with_for_update()
                )
            )
            duplicate = next(
                (version for version in versions if version.content_hash == content_hash), None
            )
            checkpoint = session.get(
                TelegramCollectorCheckpoint, message.channel_id, with_for_update=True
            )
            checkpoint_value = checkpoint.last_message_id if checkpoint is not None else 0
            if duplicate is not None:
                return TelegramPersistResult(
                    source_event_id=duplicate.source_event_id,
                    audit_event_id=duplicate.audit_event_id,
                    outbox_event_id=_stable_id("telegram-outbox", duplicate.source_event_id),
                    edit_version=duplicate.edit_version,
                    checkpoint=checkpoint_value,
                    duplicate=True,
                    media_sha256=duplicate.media_sha256,
                )

            edit_version = versions[0].edit_version + 1 if versions else 0
            media = None
            if message.media_bytes is not None:
                media = self._media_store.store(
                    channel_id=message.channel_id,
                    message_id=message.message_id,
                    edit_version=edit_version,
                    content=message.media_bytes,
                    filename=message.media_filename,
                )

            identity = f"{message.channel_id}:{message.message_id}:{edit_version}:{content_hash}"
            source_event_id = _stable_id("telegram-message", identity)
            audit_event_id = _stable_id("telegram-audit", source_event_id)
            outbox_event_id = _stable_id("telegram-outbox", source_event_id)
            aggregate_id = f"{message.channel_id}:{message.message_id}"
            audit_payload = {
                "channel_id": message.channel_id,
                "message_id": message.message_id,
                "edit_version": edit_version,
                "event_kind": message.event_kind,
                "content_hash": content_hash,
                "media_sha256": media.sha256 if media else None,
            }
            append_audit_event(
                session,
                event_id=audit_event_id,
                event_type="telegram.message.persisted",
                aggregate_type="telegram_message",
                aggregate_id=aggregate_id,
                payload=audit_payload,
            )
            append_outbox_event(
                session,
                event_id=outbox_event_id,
                event_type="telegram.message.persisted",
                aggregate_type="telegram_message",
                aggregate_id=aggregate_id,
                payload=audit_payload,
            )
            session.add(
                TelegramMessageVersion(
                    source_event_id=source_event_id,
                    channel_id=message.channel_id,
                    message_id=message.message_id,
                    edit_version=edit_version,
                    event_kind=message.event_kind,
                    is_backfill=message.event_kind == "BACKFILL",
                    source_date=message.source_date,
                    edit_date=message.edit_date,
                    received_at=message.received_at,
                    text=message.text,
                    content_type=message.content_type,
                    reply_to_message_id=message.reply_to_message_id,
                    forward_origin_type=message.forward_origin_type,
                    forward_origin_id=message.forward_origin_id,
                    forward_message_id=message.forward_message_id,
                    forward_date=message.forward_date,
                    media_sha256=media.sha256 if media else None,
                    media_path=media.relative_path if media else None,
                    media_mime_type=message.media_mime_type,
                    media_size_bytes=media.size_bytes if media else None,
                    content_hash=content_hash,
                    audit_event_id=audit_event_id,
                )
            )
            next_checkpoint = max(checkpoint_value, message.message_id)
            if checkpoint is None:
                session.add(
                    TelegramCollectorCheckpoint(
                        channel_id=message.channel_id,
                        last_message_id=next_checkpoint,
                    )
                )
            else:
                checkpoint.last_message_id = next_checkpoint

        return TelegramPersistResult(
            source_event_id=source_event_id,
            audit_event_id=audit_event_id,
            outbox_event_id=outbox_event_id,
            edit_version=edit_version,
            checkpoint=next_checkpoint,
            duplicate=False,
            media_sha256=media_sha256,
        )


def input_public_dict(message: TelegramMessageInput) -> dict[str, object]:
    """Testing/debug helper that intentionally omits raw media bytes."""
    value = asdict(message)
    value.pop("media_bytes", None)
    return value
