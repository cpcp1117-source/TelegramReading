from __future__ import annotations

from decimal import Decimal

import pytest

from telegram_trader.draft_edit import (
    DraftEditError,
    EntryEdit,
    parse_entry_input,
    parse_size_input,
    parse_take_profit_input,
)


def test_price_becomes_limit_entry() -> None:
    assert parse_entry_input(" 1.1939 ") == EntryEdit("LIMIT", Decimal("1.1939"))
    assert parse_entry_input("65,432.5") == EntryEdit("LIMIT", Decimal("65432.5"))


@pytest.mark.parametrize("word", ["市價", "市价", "market", "MARKET", " m "])
def test_market_words_become_market_entry(word: str) -> None:
    assert parse_entry_input(word) == EntryEdit("MARKET", None)


@pytest.mark.parametrize("text", ["abc", "", "0", "-1", "NaN", "Infinity"])
def test_bad_entry_is_rejected_with_a_message(text: str) -> None:
    with pytest.raises(DraftEditError):
        parse_entry_input(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10", "0.10"),
        ("10%", "0.10"),
        (" 2.5 ％", "0.025"),  # noqa: RUF001
        ("100", "1"),
    ],
)
def test_size_is_a_percentage_of_equity(text: str, expected: str) -> None:
    assert parse_size_input(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["0", "-5", "101", "abc", "%", "1e400"])
def test_bad_size_is_rejected(text: str) -> None:
    with pytest.raises(DraftEditError):
        parse_size_input(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.10", ["1.10"]),
        ("1.10, 1.05", ["1.10", "1.05"]),
        ("1.10 1.05  1.00", ["1.10", "1.05", "1.00"]),
        ("1.10，1.05", ["1.10", "1.05"]),  # noqa: RUF001
    ],
)
def test_take_profit_accepts_one_or_several_prices_in_order(text: str, expected: list[str]) -> None:
    assert parse_take_profit_input(text) == [Decimal(v) for v in expected]


@pytest.mark.parametrize("text", ["", "abc", "1.1, x", "0", "1 2 3 4 5"])
def test_bad_take_profit_is_rejected(text: str) -> None:
    with pytest.raises(DraftEditError):
        parse_take_profit_input(text)
