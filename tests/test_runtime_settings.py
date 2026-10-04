from __future__ import annotations

from decimal import Decimal

import pytest

from telegram_trader.config import Settings
from telegram_trader.runtime_settings import (
    SETTINGS,
    SPECS,
    SettingValueError,
    parse_display_value,
    to_display,
    to_stored,
)


def test_every_editable_setting_is_a_settings_field_with_an_in_range_default() -> None:
    defaults = Settings(risk_equity_baseline_usdt=Decimal("10000"))
    assert len(SPECS) == len(SETTINGS)
    for spec in SETTINGS:
        assert spec.key in Settings.model_fields, spec.key
        assert spec.group in ("risk", "runtime")
        display = to_display(spec, getattr(defaults, spec.key))
        assert display is not None
        parse_display_value(spec, display)  # the shipped default must pass its own bounds


def test_percent_settings_travel_in_display_units() -> None:
    spec = SPECS["risk_position_size_pct"]
    assert to_display(spec, Decimal("0.2")) == "20"
    assert to_stored(spec, parse_display_value(spec, "12.5")) == "0.125"
    assert to_display(spec, None) is None
    leverage = SPECS["risk_leverage"]
    assert to_stored(leverage, parse_display_value(leverage, "7")) == "7"


@pytest.mark.parametrize(
    ("key", "raw", "message"),
    [
        ("risk_leverage", "abc", "請輸入數字"),
        ("risk_leverage", "2.5", "請輸入整數"),
        ("risk_leverage", "0", "必須介於 1 到 50倍"),
        ("risk_position_size_pct", "150", "必須介於 1 到 100%"),
        ("risk_equity_baseline_usdt", "NaN", "請輸入數字"),
        ("control_bot_poll_interval_seconds", "1", "必須介於 5 到 3600秒"),
    ],
)
def test_invalid_values_are_rejected_with_a_readable_message(
    key: str, raw: str, message: str
) -> None:
    with pytest.raises(SettingValueError, match=message):
        parse_display_value(SPECS[key], raw)
