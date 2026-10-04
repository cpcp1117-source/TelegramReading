"""Parsing of the numbers a user types when editing a draft in the Control Bot.

Pure functions, no I/O. The Control Bot asks "reply with one new value"; these turn that reply into
a validated value or a short Traditional-Chinese error the bot can send straight back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

EntryKind = Literal["MARKET", "LIMIT"]

_MARKET_WORDS = frozenset({"市價", "市价", "market", "m"})
MAX_POSITION_SIZE_PCT = Decimal(100)


class DraftEditError(ValueError):
    """The reply was not a usable value; the message is safe to show the user."""


@dataclass(frozen=True, slots=True)
class EntryEdit:
    kind: EntryKind
    price: Decimal | None  # None for MARKET


def _positive_decimal(text: str, *, label: str) -> Decimal:
    try:
        value = Decimal(text.strip().replace(",", ""))
    except InvalidOperation:
        raise DraftEditError(f"{label}要是數字，例如 1.19") from None
    if not value.is_finite() or value <= 0:
        raise DraftEditError(f"{label}必須大於 0")
    return value


def parse_entry_input(text: str) -> EntryEdit:
    """A price becomes a LIMIT entry at that price; 「市價」 turns the entry into MARKET."""
    stripped = text.strip()
    if stripped.lower() in _MARKET_WORDS:
        return EntryEdit(kind="MARKET", price=None)
    return EntryEdit(kind="LIMIT", price=_positive_decimal(stripped, label="進場價"))


def parse_size_input(text: str) -> Decimal:
    """Position size as a percentage of equity used as margin, e.g. `10` or `10%` -> `0.10`."""
    stripped = text.strip().rstrip("%％").strip()
    percent = _positive_decimal(stripped, label="倉位比例")
    if percent > MAX_POSITION_SIZE_PCT:
        raise DraftEditError(f"倉位比例不能超過 {MAX_POSITION_SIZE_PCT}%（保證金佔資金的比例）")
    return percent / Decimal(100)


MAX_TAKE_PROFIT_TARGETS = 4


def parse_take_profit_input(text: str) -> list[Decimal]:
    """One or more take-profit prices separated by commas / spaces, e.g. `1.10, 1.05`.

    The filled quantity is split equally across them (at most four), so order is kept as typed.
    """
    parts = [part for part in re.split(r"[,\s、，;；]+", text.strip()) if part]
    if not parts:
        raise DraftEditError("停利要是數字，例如 1.10 或 1.10, 1.05")
    if len(parts) > MAX_TAKE_PROFIT_TARGETS:
        raise DraftEditError(f"停利最多 {MAX_TAKE_PROFIT_TARGETS} 個")
    return [_positive_decimal(part, label="停利") for part in parts]
