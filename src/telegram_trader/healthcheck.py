from __future__ import annotations

import json
import sys
from urllib.error import URLError
from urllib.request import urlopen

from telegram_trader.config import get_settings


def main() -> int:
    settings = get_settings()
    url = f"http://127.0.0.1:{settings.http_port}/health/ready"
    try:
        with urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode())
            return 0 if response.status == 200 and payload.get("status") == "ready" else 1
    except (OSError, URLError, ValueError):
        return 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
