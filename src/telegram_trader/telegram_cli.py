from __future__ import annotations

import argparse
import asyncio
import json

from telegram_trader.config import get_settings
from telegram_trader.telegram_readonly import (
    ChannelDialogSummary,
    account_summary,
    create_client,
    list_channel_dialogs,
    public_dict,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 2 Telegram read-only bootstrap")
    parser.add_argument(
        "command",
        choices=("login", "dialogs"),
        help="Login interactively or list channel dialogs without reading messages",
    )
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def dialog_listing_result(
    dialogs: list[ChannelDialogSummary], target_username: str
) -> dict[str, object]:
    """Return only the target summary so unrelated channel names stay private."""
    target = next((dialog for dialog in dialogs if dialog.is_target), None)
    return {
        "channel_count": len(dialogs),
        "target": public_dict(target) if target is not None else None,
        "target_username": target_username,
        "target_found": target is not None,
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

        dialogs = await list_channel_dialogs(client, settings.telegram_target_username)
        _print_json(dialog_listing_result(dialogs, settings.telegram_target_username))
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    args = _parser().parse_args()
    return asyncio.run(_run(args.command))


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
