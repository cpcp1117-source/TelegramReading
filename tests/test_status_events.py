from __future__ import annotations

import pytest

from telegram_trader.status_events import classify_status_message, symbols_in_text

SYMBOLS = frozenset({"SOLUSDT", "C98USDT", "AVAXUSDT", "BTCUSDT"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # real messages from the Bonnie VIP channel
        ("SOL\nTp1 !!️ 記得減倉套保", "TARGET_HIT"),
        ("C98\nTp1 !! 記得減倉套保", "TARGET_HIT"),
        ("Tp🔥", "TARGET_HIT"),
        ("直接上行接近TP1位置了\n先撤單", "CANCEL"),
        ("個人小損離場", "EXIT"),
        ("TP2 ✅", "TARGET_HIT"),
        ("目標達成 先減倉", "TARGET_HIT"),
        ("這單取消", "CANCEL"),
    ],
)
def test_status_messages_are_classified(text: str, expected: str) -> None:
    assert classify_status_message(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # a setup post is never a status event, even though it mentions TP and 撤單
        "DOT 空\nEN:1.18-1.2\nSL:1.23上方\nTP:1.08-1.1、1.03-1.05\n(如果先下跌接近目標就撤單)",
        "AVAX 多\nH4-H6指標修復\n回踩11-11.16可以嘗試介入\nSL:10.7\nTP:11.8、12.5",
        "C98\n價格突破pm vah\n回踩關注此區0.01674-0.0165 可嘗試介入\n止損於0.01625",
        "我愛邦妮",
        "",
        "   ",
    ],
)
def test_setup_posts_and_chatter_are_not_status_events(text: str) -> None:
    assert classify_status_message(text) is None


def test_long_text_is_never_a_status_event() -> None:
    assert classify_status_message("先撤單 " + "x" * 100) is None


def test_symbols_are_matched_against_the_tradable_universe() -> None:
    assert symbols_in_text("SOL\nTp1 !! 記得減倉套保", SYMBOLS) == ["SOLUSDT"]
    assert symbols_in_text("C98 Tp1", SYMBOLS) == ["C98USDT"]
    assert symbols_in_text("$AVAX 先撤單", SYMBOLS) == ["AVAXUSDT"]
    assert symbols_in_text("Tp🔥", SYMBOLS) == []
    assert symbols_in_text("SOL 和 AVAX 都 Tp1", SYMBOLS) == ["SOLUSDT", "AVAXUSDT"]
    assert symbols_in_text("H4 RSI MACD", SYMBOLS) == []
