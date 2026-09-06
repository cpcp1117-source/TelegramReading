from __future__ import annotations

import json
import logging

from telegram_trader.logging_config import JsonFormatter, configure_logging


def test_json_formatter_redacts_nested_sensitive_values() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    record.context = {
        "token": "value-never-rendered",
        "nested": {"api_key": "also-hidden", "safe": "visible"},
        "items": [{"password": "hidden"}],
        "tuple": ({"secret": "hidden"},),
        "telegram": {
            "api_id": 12345,
            "phone_number": "+000000000",
            "code": "00000",
            "two_factor": "hidden",
        },
    }

    payload = json.loads(JsonFormatter().format(record))

    assert payload["context"]["token"] == "[REDACTED]"
    assert payload["context"]["nested"]["api_key"] == "[REDACTED]"
    assert payload["context"]["nested"]["safe"] == "visible"
    assert payload["context"]["items"][0]["password"] == "[REDACTED]"
    assert payload["context"]["tuple"][0]["secret"] == "[REDACTED]"
    assert payload["context"]["telegram"]["api_id"] == "[REDACTED]"
    assert payload["context"]["telegram"]["phone_number"] == "[REDACTED]"
    assert payload["context"]["telegram"]["code"] == "[REDACTED]"
    assert payload["context"]["telegram"]["two_factor"] == "[REDACTED]"
    assert "value-never-rendered" not in json.dumps(payload)


def test_configure_logging_replaces_root_handlers() -> None:
    configure_logging("WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
