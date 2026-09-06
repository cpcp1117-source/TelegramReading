from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from telegram_trader.telegram_storage import (
    MediaStore,
    TelegramMessageInput,
    content_fingerprint,
    input_public_dict,
)

NOW = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


def message(**overrides: object) -> TelegramMessageInput:
    values: dict[str, object] = {
        "channel_id": 2439599598,
        "message_id": 100,
        "event_kind": "NEW",
        "source_date": NOW,
        "received_at": NOW,
        "text": "#CHIP market long",
        "content_type": "text",
    }
    values.update(overrides)
    return TelegramMessageInput(**values)  # type: ignore[arg-type]


def test_content_fingerprint_is_deterministic_and_transport_agnostic() -> None:
    first_hash, _ = content_fingerprint(message(event_kind="NEW"))
    replay_hash, _ = content_fingerprint(message(event_kind="BACKFILL"))

    assert first_hash == replay_hash


def test_content_fingerprint_changes_for_edit() -> None:
    original_hash, _ = content_fingerprint(message(text="original"))
    edited_hash, _ = content_fingerprint(message(text="edited", edit_date=NOW))

    assert original_hash != edited_hash


def test_media_store_hashes_and_uses_safe_extension(tmp_path: Path) -> None:
    content = b"synthetic-image-bytes"
    stored = MediaStore(tmp_path).store(
        channel_id=2439599598,
        message_id=100,
        edit_version=0,
        content=content,
        filename="../../unsafe.exe",
    )

    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert stored.relative_path.endswith(".bin")
    assert (tmp_path / stored.relative_path).read_bytes() == content


def test_message_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        message(source_date=datetime(2026, 9, 6, 8, 0))


def test_public_input_dict_omits_media_bytes() -> None:
    public = input_public_dict(message(media_bytes=b"private-media", content_type="image"))

    assert "media_bytes" not in public
    assert "private-media" not in str(public)
