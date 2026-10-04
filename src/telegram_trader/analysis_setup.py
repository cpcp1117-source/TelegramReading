"""Pure logic for analysis setups: the AI response contract and the market-confirmation rules.

Division of labour (decided with the user, 2026-10-04): the AI only *describes* an idea it found
in an analyst's message (symbol, side, entry zone, stop, targets, optional break trigger).
Everything that decides *when* a draft is raised -- the confirmation rule, invalidation, expiry --
is deterministic code in this module, because the AI proved unstable on exactly those judgement
calls (the same message sometimes yielded a "break below" trigger and sometimes none).

No database, clock or network access here: `evaluate_candles` is a pure function of the setup
state and the closed candles it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

CURRENT_SETUP_SCHEMA_VERSION = "s1"

# `normalized_signals.parser_version` of every signal a setup monitor raises is this prefix plus
# the setup id. The Risk Engine uses it to tell a monitor-built draft from a live trading call.
SETUP_PARSER_VERSION_PREFIX = "setup-"

Side = Literal["LONG", "SHORT"]
ConfirmRule = Literal["REJECT", "BREAK"]
SetupStatus = Literal["WATCHING", "ZONE_TOUCHED", "TRIGGERED", "INVALIDATED", "EXPIRED"]

_ALLOWED_SIDES = frozenset({"LONG", "SHORT", "NONE"})
_ALLOWED_ENTRY_KINDS = frozenset({"ZONE", "NONE"})
_ALLOWED_TRIGGER_TYPES = frozenset({"NONE", "BREAK_BELOW", "BREAK_ABOVE"})
_ALLOWED_CONFIDENCE = frozenset({"HIGH", "MEDIUM", "LOW"})

_SETUP_FIELDS = frozenset(
    {
        "symbol",
        "side",
        "is_update_of_earlier_message",
        "entry_kind",
        "entry_zone_low",
        "entry_zone_high",
        "trigger_type",
        "trigger_price",
        "stop_loss",
        "take_profits",
        "cancel_if_target_reached_first",
        "confidence",
        "risk_notes",
        "evidence_quotes",
    }
)
_TOP_LEVEL_FIELDS = frozenset({"summary_zh", "setups"})

SETUP_SYSTEM_PROMPT = (
    "你是交易分析整理助手。輸入是 Telegram 分析頻道的『最新訊息』，"
    "以及同一討論串較早的訊息作為上下文。"
    "請把『最新訊息』整理成可監控的交易情境 setups；最新訊息沒有交易情境時，setups 回傳空陣列。"
    "規則：1) 只能使用原文出現的價格與內容，不可捏造。"
    "2) 最新訊息若沒寫幣種，從上下文判斷它是補充哪一則；"
    "若補充了停損或目標，以最新為準，並合併較早訊息的進場區與目標。"
    "3)『平空』『平多』『空方籌碼減少』是描述籌碼，不是交易方向。"
    "4) entry_kind=ZONE 代表原文有明確的進場區間（單一價位就把 low 與 high 填同一個數字）；"
    "沒有明確進場價位就用 NONE，並把 side 設為 NONE。"
    "5) 有『跌破/站上某價才成立』的明確條件才填 trigger_type 與 trigger_price，否則 NONE。"
    "6) 原文說「如果先跌到/漲到目標就撤單」才把 cancel_if_target_reached_first 設為 true。"
    "7) symbol 用 Binance USDT 永續格式，例如 DOTUSDT；原文的價格數字一律用數字型別。"
    "8) evidence_quotes 必須是輸入文字中逐字出現的片段。summary_zh 用繁體中文 3 到 5 行摘要。"
)


def _decimal_field() -> dict[str, Any]:
    return {"type": ["number", "null"]}


SETUP_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(_TOP_LEVEL_FIELDS),
    "properties": {
        "summary_zh": {"type": "string"},
        "setups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(_SETUP_FIELDS),
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": sorted(_ALLOWED_SIDES)},
                    "is_update_of_earlier_message": {"type": "boolean"},
                    "entry_kind": {"type": "string", "enum": sorted(_ALLOWED_ENTRY_KINDS)},
                    "entry_zone_low": _decimal_field(),
                    "entry_zone_high": _decimal_field(),
                    "trigger_type": {"type": "string", "enum": sorted(_ALLOWED_TRIGGER_TYPES)},
                    "trigger_price": _decimal_field(),
                    "stop_loss": _decimal_field(),
                    "take_profits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["low", "high"],
                            "properties": {
                                "low": {"type": "number"},
                                "high": {"type": "number"},
                            },
                        },
                    },
                    "cancel_if_target_reached_first": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": sorted(_ALLOWED_CONFIDENCE)},
                    "risk_notes": {"type": "array", "items": {"type": "string"}},
                    "evidence_quotes": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


class SetupValidationError(ValueError):
    """The AI response (or one setup in it) violated the contract; nothing is stored for it."""


@dataclass(frozen=True, slots=True)
class PriceZone:
    low: Decimal
    high: Decimal


@dataclass(frozen=True, slots=True)
class ParsedSetup:
    symbol: str
    side: Side
    is_update: bool
    entry_zone: PriceZone
    confirm_rule: ConfirmRule
    trigger_price: Decimal | None
    stop_value: Decimal | None
    take_profits: list[PriceZone]
    cancel_if_target_first: bool
    confidence: str
    risk_notes: list[str]
    evidence_quotes: list[str]


@dataclass(frozen=True, slots=True)
class ParsedSetupResponse:
    summary_zh: str
    setups: list[ParsedSetup]
    rejected: list[str] = field(default_factory=list)


def _to_decimal(value: Any, *, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise SetupValidationError(f"{name} must be a number, got {value!r}")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise SetupValidationError(f"{name} is not a valid number: {value!r}") from error
    if not result.is_finite() or result <= 0:
        raise SetupValidationError(f"{name} must be a positive number, got {value!r}")
    return result


def _optional_decimal(value: Any, *, name: str) -> Decimal | None:
    return None if value is None else _to_decimal(value, name=name)


def _zone(low: Any, high: Any, *, name: str) -> PriceZone:
    low_value = _to_decimal(low, name=f"{name}.low")
    high_value = _to_decimal(high, name=f"{name}.high")
    if low_value > high_value:
        low_value, high_value = high_value, low_value
    return PriceZone(low=low_value, high=high_value)


def _check_geometry(setup: ParsedSetup) -> None:
    """Stop and targets must sit on the sides of the entry zone the direction implies."""
    zone = setup.entry_zone
    if setup.side == "SHORT":
        if setup.stop_value is not None and setup.stop_value <= zone.high:
            raise SetupValidationError("SHORT stop must be above the entry zone")
        if any(target.high >= zone.low for target in setup.take_profits):
            raise SetupValidationError("SHORT targets must be below the entry zone")
    else:
        if setup.stop_value is not None and setup.stop_value >= zone.low:
            raise SetupValidationError("LONG stop must be below the entry zone")
        if any(target.low <= zone.high for target in setup.take_profits):
            raise SetupValidationError("LONG targets must be above the entry zone")


def _parse_one(raw: Any, *, valid_symbols: frozenset[str], source_text: str) -> ParsedSetup | None:
    """One validated setup, or `None` for a setup the AI itself marked as non-actionable."""
    if not isinstance(raw, dict):
        raise SetupValidationError("setup entry is not a JSON object")
    unknown = set(raw) - _SETUP_FIELDS
    if unknown:
        raise SetupValidationError(f"unknown setup field(s): {sorted(unknown)}")
    missing = _SETUP_FIELDS - set(raw)
    if missing:
        raise SetupValidationError(f"missing setup field(s): {sorted(missing)}")

    side = raw["side"]
    if side not in _ALLOWED_SIDES:
        raise SetupValidationError(f"invalid side: {side!r}")
    if raw["entry_kind"] not in _ALLOWED_ENTRY_KINDS:
        raise SetupValidationError(f"invalid entry_kind: {raw['entry_kind']!r}")
    if side == "NONE" or raw["entry_kind"] == "NONE":
        return None

    symbol = raw["symbol"]
    if symbol not in valid_symbols:
        raise SetupValidationError(f"setup references an unresolved symbol: {symbol!r}")

    for key in ("is_update_of_earlier_message", "cancel_if_target_reached_first"):
        if not isinstance(raw[key], bool):
            raise SetupValidationError(f"{key} must be a boolean")
    if raw["confidence"] not in _ALLOWED_CONFIDENCE:
        raise SetupValidationError(f"invalid confidence: {raw['confidence']!r}")
    trigger_type = raw["trigger_type"]
    if trigger_type not in _ALLOWED_TRIGGER_TYPES:
        raise SetupValidationError(f"invalid trigger_type: {trigger_type!r}")

    for key in ("risk_notes", "evidence_quotes"):
        if not isinstance(raw[key], list) or not all(isinstance(item, str) for item in raw[key]):
            raise SetupValidationError(f"{key} must be a list of strings")
    for quote in raw["evidence_quotes"]:
        if quote not in source_text:
            raise SetupValidationError(f"evidence quote is not verbatim in the input: {quote!r}")

    take_profits_raw = raw["take_profits"]
    if not isinstance(take_profits_raw, list):
        raise SetupValidationError("take_profits must be a list")
    take_profits = [
        _zone(entry.get("low"), entry.get("high"), name="take_profit")
        for entry in take_profits_raw
        if isinstance(entry, dict)
    ]
    if len(take_profits) != len(take_profits_raw):
        raise SetupValidationError("take_profits entries must be objects")

    zone = _zone(raw["entry_zone_low"], raw["entry_zone_high"], name="entry_zone")
    trigger_price = _optional_decimal(raw["trigger_price"], name="trigger_price")

    # The break rule is used only when the AI's trigger agrees with the trade direction;
    # anything else (missing, contradictory) falls back to the code's default "rejection" rule.
    confirm_rule: ConfirmRule = "REJECT"
    if trigger_price is not None and (
        (side == "SHORT" and trigger_type == "BREAK_BELOW")
        or (side == "LONG" and trigger_type == "BREAK_ABOVE")
    ):
        confirm_rule = "BREAK"
    else:
        trigger_price = None

    setup = ParsedSetup(
        symbol=symbol,
        side=side,
        is_update=raw["is_update_of_earlier_message"],
        entry_zone=zone,
        confirm_rule=confirm_rule,
        trigger_price=trigger_price,
        stop_value=_optional_decimal(raw["stop_loss"], name="stop_loss"),
        take_profits=take_profits,
        cancel_if_target_first=raw["cancel_if_target_reached_first"],
        confidence=raw["confidence"],
        risk_notes=list(raw["risk_notes"]),
        evidence_quotes=list(raw["evidence_quotes"]),
    )
    _check_geometry(setup)
    return setup


def parse_setup_response(
    raw: Any, *, valid_symbols: frozenset[str], source_text: str
) -> ParsedSetupResponse:
    """Validate the AI's JSON. One bad setup goes to `rejected`; it never poisons the rest."""
    if not isinstance(raw, dict):
        raise SetupValidationError("response is not a JSON object")
    unknown = set(raw) - _TOP_LEVEL_FIELDS
    if unknown:
        raise SetupValidationError(f"unknown top-level field(s): {sorted(unknown)}")
    missing = _TOP_LEVEL_FIELDS - set(raw)
    if missing:
        raise SetupValidationError(f"missing required field(s): {sorted(missing)}")
    if not isinstance(raw["summary_zh"], str) or not isinstance(raw["setups"], list):
        raise SetupValidationError("summary_zh must be a string and setups a list")

    setups: list[ParsedSetup] = []
    rejected: list[str] = []
    for entry in raw["setups"]:
        try:
            parsed = _parse_one(entry, valid_symbols=valid_symbols, source_text=source_text)
        except SetupValidationError as error:
            rejected.append(str(error))
            continue
        if parsed is not None:
            setups.append(parsed)
    return ParsedSetupResponse(summary_zh=raw["summary_zh"], setups=setups, rejected=rejected)


# --------------------------------------------------------------------------- market rules


@dataclass(frozen=True, slots=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class SetupState:
    """The slice of an `analysis_setups` row the market rules read."""

    side: Side
    status: SetupStatus
    entry_zone: PriceZone
    confirm_rule: ConfirmRule
    trigger_price: Decimal | None
    stop_value: Decimal | None
    first_target: PriceZone | None
    cancel_if_target_first: bool
    valid_from: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SetupTransition:
    """What changed after replaying the candles; `status` is the new status."""

    status: SetupStatus
    reason: str | None = None
    touched_at: datetime | None = None
    triggered_at: datetime | None = None
    entry_price: Decimal | None = None
    events: tuple[str, ...] = ()
    last_candle_close_time: datetime | None = None


def _touches_zone(candle: Candle, zone: PriceZone) -> bool:
    return candle.high >= zone.low and candle.low <= zone.high


def _is_confirmed(state: SetupState, candle: Candle) -> bool:
    zone = state.entry_zone
    if state.confirm_rule == "BREAK" and state.trigger_price is not None:
        if state.side == "SHORT":
            return candle.close < state.trigger_price
        return candle.close > state.trigger_price
    # REJECT: the candle reached the zone but closed back out of it in the trade's direction.
    if state.side == "SHORT":
        return candle.high >= zone.low and candle.close < candle.open and candle.close < zone.high
    return candle.low <= zone.high and candle.close > candle.open and candle.close > zone.low


def _is_invalidated(state: SetupState, candle: Candle) -> str | None:
    if state.side == "SHORT":
        if state.stop_value is not None and candle.high >= state.stop_value:
            return "STOP_REACHED"
        if (
            state.cancel_if_target_first
            and state.first_target is not None
            and candle.low <= state.first_target.high
        ):
            return "TARGET_REACHED_FIRST"
    else:
        if state.stop_value is not None and candle.low <= state.stop_value:
            return "STOP_REACHED"
        if (
            state.cancel_if_target_first
            and state.first_target is not None
            and candle.high >= state.first_target.low
        ):
            return "TARGET_REACHED_FIRST"
    return None


def _clamp(value: Decimal, zone: PriceZone) -> Decimal:
    return min(max(value, zone.low), zone.high)


def evaluate_candles(
    state: SetupState,
    candles: list[Candle],
    *,
    now: datetime,
    max_trigger_age: timedelta | None = None,
) -> SetupTransition:
    """Replay closed candles (oldest first) against a `WATCHING`/`ZONE_TOUCHED` setup.

    Only candles that opened at or after `valid_from` and have already closed by `now` count --
    the analyst's idea cannot be "confirmed" by price action that predates the message, and an
    unfinished candle can still reverse. Per candle, in order: invalidation, zone touch, then
    confirmation. A confirmation needs a prior (or same-candle) touch of the entry zone.
    """
    status: SetupStatus = state.status
    touched_at: datetime | None = None
    events: list[str] = []
    last_close_time: datetime | None = None

    for candle in candles:
        if candle.open_time < state.valid_from or candle.close_time > now:
            continue
        last_close_time = candle.close_time
        if candle.close_time > state.expires_at:
            return SetupTransition(
                status="EXPIRED",
                reason="EXPIRED",
                touched_at=touched_at,
                events=tuple(events),
                last_candle_close_time=last_close_time,
            )

        reason = _is_invalidated(state, candle)
        if reason is not None:
            return SetupTransition(
                status="INVALIDATED",
                reason=reason,
                touched_at=touched_at,
                events=tuple(events),
                last_candle_close_time=last_close_time,
            )

        if status == "WATCHING" and _touches_zone(candle, state.entry_zone):
            status = "ZONE_TOUCHED"
            touched_at = candle.close_time
            events.append("ZONE_TOUCHED")

        if status == "ZONE_TOUCHED" and _is_confirmed(state, candle):
            if max_trigger_age is not None and now - candle.close_time > max_trigger_age:
                # Confirmed long ago (e.g. the worker was down): the market has moved on, so a
                # draft built from that candle would be stale. Close it instead of raising one.
                return SetupTransition(
                    status="INVALIDATED",
                    reason="TRIGGER_STALE",
                    last_candle_close_time=last_close_time,
                )
            return SetupTransition(
                status="TRIGGERED",
                touched_at=touched_at,
                triggered_at=candle.close_time,
                entry_price=_clamp(candle.close, state.entry_zone),
                events=(*events, "TRIGGERED"),
                last_candle_close_time=last_close_time,
            )

    if now > state.expires_at:
        return SetupTransition(
            status="EXPIRED",
            reason="EXPIRED",
            touched_at=touched_at,
            events=tuple(events),
            last_candle_close_time=last_close_time,
        )
    return SetupTransition(
        status=status,
        touched_at=touched_at,
        events=tuple(events),
        last_candle_close_time=last_close_time,
    )
