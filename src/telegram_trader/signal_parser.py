from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# Bumped v1 -> v2 with NO parser-logic change: normalization.CURRENT_NORMALIZER_VERSION
# was bumped v2 -> v3 (Phase 5 Slice 1, dynamic symbol resolution against a real
# Binance exchange snapshot), and normalized_signals' uniqueness constraint plus
# SignalParseCheckpoint's cursor are both scoped per parser_version -- this bump
# exists solely to force parse_signals.py to reprocess the backlog against the
# new v3 normalized_content rows, not to change any parsing behavior.
CURRENT_PARSER_VERSION = "v2"

Side = Literal["LONG", "SHORT"]
EntryType = Literal["MARKET", "LIMIT", "RANGE"]
StopOrigin = Literal["AUTHOR", "DEFAULT_ROE_30", "NONE"]
SignalStatus = Literal["NEW", "INCOMPLETE", "VALIDATED", "CANCELLED"]

_WAIT_KEYWORDS = ("觀望", "观望", "wait")
_LONG_KEYWORDS = ("多", "long", "buy")
_SHORT_KEYWORDS = ("空", "short", "sell")
_LONG_FALSE_POSITIVES = ("多少", "差不多", "許多", "许多", "大多", "太多", "很多", "多次", "多久")
_SHORT_FALSE_POSITIVES = ("空間", "空间", "時空", "时空", "有空", "空的", "抽空")
_NEGATION_MARKERS = ("不要", "別", "别", "先不")
_LIMIT_KEYWORDS = ("限價", "限价", "掛單", "挂单", "limit")
_ENTRY_REFERENCE_KEYWORDS = ("進場", "进场", "entry", "@", "附近")
_STOP_KEYWORDS = ("止損", "止损", "stop loss", "sl")
_TAKE_PROFIT_KEYWORDS = ("止盈", "目標價", "目标价", "目標", "目标", "take profit", "tp")
_CANCEL_KEYWORDS = ("取消", "平倉", "平仓", "cancel", "close")

_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_NUMBER_WINDOW = 20


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _NumberMatch:
    value: Decimal
    span: EvidenceSpan


@dataclass(frozen=True, slots=True)
class ParsedFields:
    """Deterministic, best-effort extraction from one message's normalized text.

    This is a heuristic keyword/regex matcher, not NLP -- it is grounded in
    the real fixtures documented in monster-currency-universe.md, but is
    expected to need iteration as more real message shapes are seen (same
    "versioned, reprocess on bump" idiom as normalization.py).
    """

    side: Side | None
    side_span: EvidenceSpan | None
    entry_type: EntryType
    entry_values: list[Decimal]
    entry_spans: list[EvidenceSpan]
    stop_value: Decimal | None
    stop_span: EvidenceSpan | None
    take_profits: list[Decimal]
    take_profit_spans: list[EvidenceSpan]
    cancel_detected: bool
    cancel_span: EvidenceSpan | None
    is_signal_attempt: bool


def _find_numbers_after(
    text: str, start: int, *, max_count: int = 2, window: int = _NUMBER_WINDOW
) -> list[_NumberMatch]:
    segment = text[start : start + window]
    matches = list(_NUMBER_PATTERN.finditer(segment))[:max_count]
    return [
        _NumberMatch(
            Decimal(match.group(0)), EvidenceSpan(start + match.start(), start + match.end())
        )
        for match in matches
    ]


def _find_direction_keyword(
    lower_text: str, keywords: tuple[str, ...], false_positives: tuple[str, ...]
) -> EvidenceSpan | None:
    for keyword in keywords:
        search_start = 0
        while True:
            index = lower_text.find(keyword, search_start)
            if index == -1:
                break
            window = lower_text[max(0, index - 2) : index + len(keyword) + 2]
            if not any(fp in window for fp in false_positives):
                return EvidenceSpan(index, index + len(keyword))
            search_start = index + len(keyword)
    return None


def _is_negated(lower_text: str, span: EvidenceSpan) -> bool:
    window = lower_text[max(0, span.start - 6) : span.start]
    return any(marker in window for marker in _NEGATION_MARKERS)


def _extract_side(lower_text: str) -> tuple[Side | None, EvidenceSpan | None]:
    long_span = _find_direction_keyword(lower_text, _LONG_KEYWORDS, _LONG_FALSE_POSITIVES)
    short_span = _find_direction_keyword(lower_text, _SHORT_KEYWORDS, _SHORT_FALSE_POSITIVES)
    if long_span is not None and short_span is not None:
        return None, None  # both directions mentioned -- ambiguous, fail closed
    span = long_span or short_span
    if span is None:
        return None, None
    if _is_negated(lower_text, span):
        return None, None
    side: Side = "LONG" if long_span is not None else "SHORT"
    return side, span


def _extract_entry(
    text: str, lower_text: str
) -> tuple[EntryType, list[Decimal], list[EvidenceSpan]]:
    for keyword in _LIMIT_KEYWORDS:
        index = lower_text.find(keyword)
        if index == -1:
            continue
        numbers = _find_numbers_after(text, index + len(keyword), max_count=2)
        if numbers:
            entry_type: EntryType = "RANGE" if len(numbers) > 1 else "LIMIT"
            return entry_type, [n.value for n in numbers], [n.span for n in numbers]
    for keyword in _ENTRY_REFERENCE_KEYWORDS:
        index = lower_text.find(keyword)
        if index == -1:
            continue
        numbers = _find_numbers_after(text, index + len(keyword), max_count=1)
        if numbers:
            return "MARKET", [n.value for n in numbers], [n.span for n in numbers]
    return "MARKET", [], []


def _extract_stop(text: str, lower_text: str) -> tuple[Decimal | None, EvidenceSpan | None]:
    for keyword in _STOP_KEYWORDS:
        index = lower_text.find(keyword)
        if index == -1:
            continue
        numbers = _find_numbers_after(text, index + len(keyword), max_count=1)
        if numbers:
            return numbers[0].value, numbers[0].span
    return None, None


def _extract_take_profits(text: str, lower_text: str) -> tuple[list[Decimal], list[EvidenceSpan]]:
    for keyword in _TAKE_PROFIT_KEYWORDS:
        index = lower_text.find(keyword)
        if index == -1:
            continue
        numbers = _find_numbers_after(text, index + len(keyword), max_count=2)
        if numbers:
            return [n.value for n in numbers], [n.span for n in numbers]
    return [], []


def _extract_cancel(lower_text: str) -> tuple[bool, EvidenceSpan | None]:
    for keyword in _CANCEL_KEYWORDS:
        index = lower_text.find(keyword)
        if index != -1:
            return True, EvidenceSpan(index, index + len(keyword))
    return False, None


def parse_fields(normalized_text: str) -> ParsedFields:
    """Extract side/entry/SL/TP/cancel from one message's normalized text.

    A WAIT marker (e.g. "建議觀望") forces `is_signal_attempt=False`
    unconditionally, even if a symbol or stray keyword is present elsewhere
    in the message (MONSTER-010). Otherwise, a message only counts as a
    signal *attempt* if a side keyword, an entry/SL/TP number, or a cancel
    keyword was found -- plain symbol mentions and promotional text (about
    half of this channel's real traffic) must not become signal rows.
    """
    lower_text = normalized_text.lower()
    if any(keyword in lower_text for keyword in _WAIT_KEYWORDS):
        return ParsedFields(
            side=None,
            side_span=None,
            entry_type="MARKET",
            entry_values=[],
            entry_spans=[],
            stop_value=None,
            stop_span=None,
            take_profits=[],
            take_profit_spans=[],
            cancel_detected=False,
            cancel_span=None,
            is_signal_attempt=False,
        )

    side, side_span = _extract_side(lower_text)
    entry_type, entry_values, entry_spans = _extract_entry(normalized_text, lower_text)
    stop_value, stop_span = _extract_stop(normalized_text, lower_text)
    take_profits, take_profit_spans = _extract_take_profits(normalized_text, lower_text)
    cancel_detected, cancel_span = _extract_cancel(lower_text)

    is_signal_attempt = bool(
        side or entry_values or stop_value is not None or take_profits or cancel_detected
    )

    return ParsedFields(
        side=side,
        side_span=side_span,
        entry_type=entry_type,
        entry_values=entry_values,
        entry_spans=entry_spans,
        stop_value=stop_value,
        stop_span=stop_span,
        take_profits=take_profits,
        take_profit_spans=take_profit_spans,
        cancel_detected=cancel_detected,
        cancel_span=cancel_span,
        is_signal_attempt=is_signal_attempt,
    )


def resolve_stop(
    stop_value: Decimal | None,
    side: Side | None,
    entry_reference: Decimal | None,
) -> tuple[Decimal | None, StopOrigin]:
    """BR-004: a valid authored stop wins; a missing one falls back to

    `DEFAULT_ROE_30` (the actual -30% ROE price is a Risk Engine
    computation in a later phase, needing leverage/fill price this phase
    doesn't have). A stop on the wrong side of a *known* entry reference
    price is treated the same as no usable stop, rather than trusted as
    self-contradictory. When no entry reference price exists at all (the
    common bare-MARKET case), the author's stop is accepted without side
    validation -- documented limitation, untested against real data.
    """
    if stop_value is None:
        return None, "DEFAULT_ROE_30"
    if side is not None and entry_reference is not None:
        if side == "LONG" and stop_value >= entry_reference:
            return None, "DEFAULT_ROE_30"
        if side == "SHORT" and stop_value <= entry_reference:
            return None, "DEFAULT_ROE_30"
    return stop_value, "AUTHOR"


def single_resolved_symbol(resolved_symbols: list[dict[str, str]]) -> tuple[str, str] | None:
    """Exactly one candidate resolves; zero or multiple fail closed (FR-002 spirit)."""
    if len(resolved_symbols) != 1:
        return None
    entry = resolved_symbols[0]
    return entry["symbol"], entry["status"]


def determine_status(symbol_status: str | None, side: Side | None) -> SignalStatus:
    """`PENDING_MARKET_DATA` (dynamic-scope channels, e.g. @followgerry) always

    yields `NEW`, never `VALIDATED` -- Phase 4 has no market data to confirm
    the symbol is an actual eligible contract, so it is never presented as
    actionable until a later phase resolves it and reprocesses.
    """
    if symbol_status is None or symbol_status == "INVALID" or side is None:
        return "INCOMPLETE"
    if symbol_status == "PENDING_MARKET_DATA":
        return "NEW"
    if symbol_status == "VALID":
        return "VALIDATED"
    raise ValueError(f"unknown symbol status: {symbol_status!r}")
