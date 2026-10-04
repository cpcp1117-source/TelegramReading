"""Print a WEB_CONSOLE_PASSWORD_HASH value.

Usage: `python -m telegram_trader.web_console.hash_password`
"""

from __future__ import annotations

import getpass
import sys

from telegram_trader.web_console.auth import hash_password

MIN_LENGTH = 12


def main() -> int:
    first = getpass.getpass("新密碼: ")
    if len(first) < MIN_LENGTH:
        print(f"密碼至少需要 {MIN_LENGTH} 個字元", file=sys.stderr)
        return 1
    if getpass.getpass("再輸入一次: ") != first:
        print("兩次輸入不一致", file=sys.stderr)
        return 1
    print(hash_password(first))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
