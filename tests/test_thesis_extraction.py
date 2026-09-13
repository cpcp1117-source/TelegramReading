from __future__ import annotations

from typing import Any

import pytest

from telegram_trader.thesis_extraction import (
    THESIS_JSON_SCHEMA,
    ThesisValidationError,
    parse_llm_thesis_response,
)

SOURCE_TEXT = "BTC 若跌破773將測試760-756支撐，不破則有機會再次上行測試790"  # noqa: RUF001
VALID_SYMBOLS = frozenset({"BTCUSDT"})


def _condition(**overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "comparator": "BELOW",
        "trigger_price": "773",
        "symbol": "BTCUSDT",
        "invalidates_thesis": True,
        "implication_direction": "DOWN",
        "target_zone_low": "756",
        "target_zone_high": "760",
        "evidence_quote": "若跌破773將測試760-756支撐",
    }
    values.update(overrides)
    return values


def _response(**overrides: object) -> dict[str, Any]:
    values: dict[str, Any] = {
        "primary_direction": "NEUTRAL",
        "confidence_status": "HIGH",
        "evidence_quotes": ["若跌破773將測試760-756支撐"],
        "conditions": [_condition()],
    }
    values.update(overrides)
    return values


def test_parse_llm_thesis_response_accepts_valid_input() -> None:
    parsed = parse_llm_thesis_response(
        _response(), valid_symbols=VALID_SYMBOLS, source_text=SOURCE_TEXT
    )

    assert parsed.primary_direction == "NEUTRAL"
    assert parsed.confidence_status == "HIGH"
    assert len(parsed.conditions) == 1
    condition = parsed.conditions[0]
    assert condition.comparator == "BELOW"
    assert str(condition.trigger_price) == "773"
    assert condition.target_zone_low is not None and str(condition.target_zone_low) == "756"


def test_parse_llm_thesis_response_accepts_insufficient_data_with_empty_conditions() -> None:
    parsed = parse_llm_thesis_response(
        _response(confidence_status="INSUFFICIENT_DATA", evidence_quotes=[], conditions=[]),
        valid_symbols=VALID_SYMBOLS,
        source_text=SOURCE_TEXT,
    )

    assert parsed.confidence_status == "INSUFFICIENT_DATA"
    assert parsed.conditions == []


def test_parse_llm_thesis_response_accepts_null_target_zone() -> None:
    parsed = parse_llm_thesis_response(
        _response(conditions=[_condition(target_zone_low=None, target_zone_high=None)]),
        valid_symbols=VALID_SYMBOLS,
        source_text=SOURCE_TEXT,
    )

    assert parsed.conditions[0].target_zone_low is None
    assert parsed.conditions[0].target_zone_high is None


def test_parse_llm_thesis_response_rejects_unknown_top_level_field() -> None:
    with pytest.raises(ThesisValidationError, match="unknown top-level field"):
        parse_llm_thesis_response(
            {**_response(), "extra_field": "surprise"},
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_missing_top_level_field() -> None:
    response = _response()
    del response["confidence_status"]

    with pytest.raises(ThesisValidationError, match="missing required field"):
        parse_llm_thesis_response(response, valid_symbols=VALID_SYMBOLS, source_text=SOURCE_TEXT)


def test_parse_llm_thesis_response_rejects_invalid_primary_direction() -> None:
    with pytest.raises(ThesisValidationError, match="primary_direction"):
        parse_llm_thesis_response(
            _response(primary_direction="SIDEWAYS"),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_unknown_condition_field() -> None:
    with pytest.raises(ThesisValidationError, match="unknown condition field"):
        parse_llm_thesis_response(
            _response(conditions=[{**_condition(), "extra": "nope"}]),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_missing_condition_field() -> None:
    condition = _condition()
    del condition["trigger_price"]

    with pytest.raises(ThesisValidationError, match="missing condition field"):
        parse_llm_thesis_response(
            _response(conditions=[condition]), valid_symbols=VALID_SYMBOLS, source_text=SOURCE_TEXT
        )


def test_parse_llm_thesis_response_rejects_invalid_comparator() -> None:
    with pytest.raises(ThesisValidationError, match="comparator"):
        parse_llm_thesis_response(
            _response(conditions=[_condition(comparator="EQUAL")]),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_non_decimal_price() -> None:
    with pytest.raises(ThesisValidationError, match="trigger_price"):
        parse_llm_thesis_response(
            _response(conditions=[_condition(trigger_price="not-a-number")]),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_invented_symbol() -> None:
    with pytest.raises(ThesisValidationError, match="unresolved symbol"):
        parse_llm_thesis_response(
            _response(conditions=[_condition(symbol="DOGEUSDT")]),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_invented_condition_evidence_quote() -> None:
    with pytest.raises(ThesisValidationError, match="verbatim substring"):
        parse_llm_thesis_response(
            _response(conditions=[_condition(evidence_quote="這句話並不存在於原文")]),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_invented_top_level_evidence_quote() -> None:
    with pytest.raises(ThesisValidationError, match="verbatim substring"):
        parse_llm_thesis_response(
            _response(evidence_quotes=["這句話並不存在於原文"]),
            valid_symbols=VALID_SYMBOLS,
            source_text=SOURCE_TEXT,
        )


def test_parse_llm_thesis_response_rejects_non_dict_input() -> None:
    with pytest.raises(ThesisValidationError, match="not a JSON object"):
        parse_llm_thesis_response(
            ["not", "a", "dict"], valid_symbols=VALID_SYMBOLS, source_text=SOURCE_TEXT
        )


def _collect_object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every nested object-typed schema node, for structural validation."""
    found: list[dict[str, Any]] = []
    if schema.get("type") == "object":
        found.append(schema)
    for value in schema.get("properties", {}).values():
        found.extend(_collect_object_schemas(value))
    items = schema.get("items")
    if isinstance(items, dict):
        found.extend(_collect_object_schemas(items))
    return found


def test_thesis_json_schema_is_strict_at_every_nesting_level() -> None:
    for node in _collect_object_schemas(THESIS_JSON_SCHEMA):
        assert node.get("additionalProperties") is False
        properties = set(node.get("properties", {}))
        required = set(node.get("required", []))
        assert properties == required, f"required must list every property: {node}"
