from __future__ import annotations

import argparse
from pathlib import Path

from telegram_trader.secret_scan import scan


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail on likely committed secrets")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    count, findings = scan(args.root.resolve())
    for finding in findings:
        print(finding)
    print(f"files_scanned={count} findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
