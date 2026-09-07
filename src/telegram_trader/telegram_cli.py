from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from telegram_trader.config import TelegramChannelTarget, get_settings
from telegram_trader.telegram_readonly import (
    ChannelDialogSummary,
    account_summary,
    create_client,
    find_target_dialogs,
    list_private_channel_dialogs,
    preview_recent_messages,
    public_dict,
    resolve_channel_entities,
)

PREVIEW_MESSAGE_LIMIT = 20


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2 Telegram read-only bootstrap")
    parser.add_argument(
        "command",
        choices=("login", "dialogs", "discover-private", "preview"),
        help=(
            "Login interactively, list the configured target channel dialogs, "
            "discover already-joined channels with no public username "
            "(for private-channel onboarding), or preview the most recent "
            "messages per configured target for onboarding fixture review "
            "(read-only: never persists or downloads media)"
        ),
    )
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def target_dialog_listing_result(
    targets: Sequence[TelegramChannelTarget],
    found_dialogs: list[ChannelDialogSummary],
) -> dict[str, object]:
    """Report found/not-found per configured target; unrelated channel names never appear."""
    found_by_channel = {dialog.channel_id: dialog for dialog in found_dialogs}
    return {
        "targets": [
            {
                "label": target.label,
                "channel_id": target.channel_id,
                "topic_id": target.topic_id,
                "found": target.channel_id in found_by_channel,
                "title": (
                    found_by_channel[target.channel_id].title
                    if target.channel_id in found_by_channel
                    else None
                ),
            }
            for target in targets
        ]
    }


async def _run(command: str) -> int:
    settings = get_settings()
    client = create_client(settings)
    try:
        # Telethon prompts for phone, OTP, and 2FA directly in this terminal.
        await client.start()
        if command == "login":
            _print_json(public_dict(await account_summary(client)))
            return 0

        if command == "discover-private":
            private_dialogs = await list_private_channel_dialogs(client)
            _print_json([public_dict(dialog) for dialog in private_dialogs])
            return 0

        if command == "preview":
            entities = await resolve_channel_entities(client, settings.telegram_target_channels)
            targets_preview = []
            for target in settings.telegram_target_channels:
                messages = await preview_recent_messages(
                    client,
                    entities[target.channel_id],
                    target.topic_id,
                    limit=PREVIEW_MESSAGE_LIMIT,
                )
                targets_preview.append(
                    {
                        "label": target.label,
                        "channel_id": target.channel_id,
                        "topic_id": target.topic_id,
                        "messages": messages,
                    }
                )
            _print_json({"targets": targets_preview})
            return 0

        dialogs = await find_target_dialogs(client, settings.telegram_target_channels)
        _print_json(target_dialog_listing_result(settings.telegram_target_channels, dialogs))
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    args = _parser().parse_args()
    return asyncio.run(_run(args.command))


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
