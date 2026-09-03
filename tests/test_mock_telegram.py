from __future__ import annotations

import json

import pytest

from telegram_trader.mock_telegram import MockTelegramMessage, load_fixture


def test_source_identity_is_deterministic_and_content_independent() -> None:
    original = MockTelegramMessage("channel", 10, 0, 1, "first")
    replay = MockTelegramMessage("channel", 10, 0, 1, "changed transport copy")
    edit = MockTelegramMessage("channel", 10, 1, 2, "edited")

    assert original.source_event_id == replay.source_event_id
    assert original.content_hash != replay.content_hash
    assert original.source_event_id != edit.source_event_id


def test_invalid_message_identity_fails_closed() -> None:
    with pytest.raises(ValueError, match="message_id"):
        MockTelegramMessage("channel", 0, 0, 1, "invalid")

    with pytest.raises(ValueError, match="channel_id"):
        MockTelegramMessage(" ", 1, 0, 1, "invalid")

    with pytest.raises(ValueError, match="edit_version"):
        MockTelegramMessage("channel", 1, -1, 1, "invalid")

    with pytest.raises(ValueError, match="sequence"):
        MockTelegramMessage("channel", 1, 0, 0, "invalid")


def test_fixture_requires_array() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        load_fixture(json.dumps({"message_id": 1}))


def test_fixture_loads_messages() -> None:
    messages = load_fixture(
        json.dumps(
            [
                {
                    "channel_id": "mock",
                    "message_id": 1,
                    "edit_version": 0,
                    "sequence": 1,
                    "text": "synthetic",
                }
            ]
        )
    )
    assert messages == [MockTelegramMessage("mock", 1, 0, 1, "synthetic")]
    assert messages[0].audit_payload()["text"] == "synthetic"
