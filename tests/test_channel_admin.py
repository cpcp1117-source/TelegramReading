from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from telegram_trader.channel_admin import (
    ChannelValueError,
    parse_channel_identity,
    validate_fields,
)


def _form(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "label": "新分析頻道",
        "username": "@new_channel",
        "channel_type": "ANALYSIS",
        "gate_decision": "MONITOR_ONLY",
        "access_authorization": "GRANTED",
        "automation_authorization": "GRANTED",
        "ai_authorization": "PENDING",
        "media_authorization": "UNKNOWN",
        "symbol_scope_mode": "STATIC_ALLOWLIST",
        "allowed_symbols": "btcusdt, ETHUSDT，ethusdt",  # full-width comma too
        "prohibited_symbols": "",
        "message_languages": "zh-TW, en",
        "supported_content_types": ["IMAGE", "TEXT"],
        "raw_retention_days": "60",
        "onboarding_doc_path": "",
        "acceptance_owner": "User",
        "acceptance_date": "2026-10-05",
    }
    values.update(overrides)
    return values


def test_validate_fields_normalizes_form_input() -> None:
    values = validate_fields(_form())
    assert values["username"] == "new_channel"
    assert values["allowed_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert values["prohibited_symbols"] == []
    assert values["message_languages"] == ["zh-TW", "en"]
    assert values["supported_content_types"] == ["TEXT", "IMAGE"]
    assert values["raw_retention_days"] == 60
    assert values["acceptance_date"] == date(2026, 10, 5)
    assert values["onboarding_doc_path"] is None
    assert validate_fields(_form(username=""))["username"] is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"label": " "}, "名稱"),
        ({"username": "a b"}, "公開帳號"),
        ({"channel_type": "SPAM"}, "頻道類型"),
        ({"ai_authorization": "YES"}, "AI 處理授權"),
        ({"allowed_symbols": ""}, "至少要填一個"),
        ({"allowed_symbols": "BTC/USDT"}, "BTC/USDT"),
        ({"supported_content_types": ["VIDEO"]}, "內容類型"),
        ({"raw_retention_days": "0"}, "1 到 3650"),
        ({"raw_retention_days": "abc"}, "整數"),
        ({"acceptance_date": "05/10/2026"}, "YYYY-MM-DD"),
    ],
)
def test_validate_fields_rejects_bad_input(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ChannelValueError, match=message):
        validate_fields(_form(**overrides))


def test_all_perpetuals_scope_does_not_need_an_allowlist() -> None:
    values = validate_fields(
        _form(symbol_scope_mode="BINANCE_USDM_ACTIVE_PERPETUAL", allowed_symbols="")
    )
    assert values["allowed_symbols"] == []


def test_channel_identity_accepts_telegram_minus_100_form() -> None:
    assert parse_channel_identity("-1002439599598", "") == (2439599598, 0)
    assert parse_channel_identity("2382278102", "21") == (2382278102, 21)
    for channel, topic, message in (
        ("abc", "0", "請輸入數字"),
        ("-5", "0", "正數"),
        ("123", "-1", "負數"),
    ):
        with pytest.raises(ChannelValueError, match=message):
            parse_channel_identity(channel, topic)
