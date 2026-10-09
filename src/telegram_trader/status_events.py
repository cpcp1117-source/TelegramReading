"""Recognising analyst *status* messages ("Tp1 !! 記得減倉套保", "先撤單", "個人小損離場").

These are events about an idea that was already posted, not new ideas. Treating them as new
analyses re-armed finished setups and kept watching ideas the analyst had already called off (found
2026-10-07 on SOL, C98 and AVAX). Pure functions, no I/O: the deterministic classifier below is the
first line of defence; the AI's own `message_kind` (see `analysis_setup.py`) catches phrasings this
misses.
"""

from __future__ import annotations

import re
from typing import Literal

StatusKind = Literal["TARGET_HIT", "CANCEL", "EXIT"]

# A status message is short. Longer text is an analysis even if it happens to contain "撤單"
# (e.g. a setup post saying "如果先下跌接近目標就撤單" is a *condition*, not a cancellation).
_MAX_STATUS_LENGTH = 80

# Anything that looks like a fresh setup (labelled prices, an entry phrase) is never a status event.
_SETUP_STRUCTURE = re.compile(
    r"(?i)(?:\bsl\b|\ben\b|\btp\d?\b)\s*[:：]\s*\d"
    r"|(?:止損|止损|止盈|進場|进场|入場|入场)\s*[:：於于]?\s*\d"
    r"|介入|嘗試|尝试"
)

_CANCEL = re.compile(r"撤單|撤单|取消|先撤|不做了|作廢|作废|(?i:\bcancel)")
_EXIT = re.compile(r"離場|离场|出場|出场|平倉|平仓|小損|小损|認賠|认赔|止損出|止损出")
_TARGET_HIT = re.compile(
    r"(?i)(?<![a-z])tp\s*[1-4]?\s*(?:[!！🔥✅🎯💰]|$)"
    r"|(?:止盈|達標|达标|到達目標|到达目标|目標達成|目标达成|減倉|减仓|套保|保本)"
)

_TOKEN = re.compile(r"[A-Za-z0-9]{2,12}")


def classify_status_message(text: str) -> StatusKind | None:
    """`TARGET_HIT`, `CANCEL` or `EXIT` for a short status message, else `None`.

    Cancel wins over target-hit ("接近TP1位置了 先撤單" mentions TP1 but means: called off).
    """
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_STATUS_LENGTH:
        return None
    if _SETUP_STRUCTURE.search(stripped):
        return None
    if _CANCEL.search(stripped):
        return "CANCEL"
    if _EXIT.search(stripped):
        return "EXIT"
    if _TARGET_HIT.search(stripped):
        return "TARGET_HIT"
    return None


def symbols_in_text(text: str, valid_symbols: frozenset[str]) -> list[str]:
    """Binance symbols named in `text` ("SOL" -> "SOLUSDT"), in order of appearance, deduplicated.

    Only tokens whose `<TOKEN>USDT` is a known tradable symbol count, so "Tp1" or "H4" never match.
    """
    found: list[str] = []
    for token in _TOKEN.findall(text):
        symbol = f"{token.upper()}USDT"
        if symbol in valid_symbols and symbol not in found:
            found.append(symbol)
    return found


STATUS_LABELS: dict[str, str] = {
    "TARGET_HIT": "分析師回報停利目標已達",
    "CANCEL": "分析師說撤單",
    "EXIT": "分析師說離場",
}
