from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy import ColumnElement, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.ai_authorization import (
    AiContentAuthorizationSnapshot,
    evaluate_channel_ai_authorization,
    evaluate_message_ai_processing,
)
from telegram_trader.models import ChannelPolicy, ThesisExtractionCheckpoint
from telegram_trader.models import NormalizedContent as NormalizedContentRow
from telegram_trader.models import Thesis as ThesisRow
from telegram_trader.openai_client import OpenAiClientError

LOGGER = logging.getLogger(__name__)

CURRENT_EXTRACTION_SCHEMA_VERSION = "t1"

_ALLOWED_PRIMARY_DIRECTIONS = frozenset({"BULLISH", "BEARISH", "NEUTRAL"})
_ALLOWED_CONFIDENCE_STATUSES = frozenset({"HIGH", "MEDIUM", "LOW", "INSUFFICIENT_DATA"})
_ALLOWED_COMPARATORS = frozenset({"BELOW", "ABOVE"})
_ALLOWED_IMPLICATION_DIRECTIONS = frozenset({"UP", "DOWN", "NEUTRAL"})

_TOP_LEVEL_FIELDS = frozenset(
    {"primary_direction", "confidence_status", "evidence_quotes", "conditions"}
)
_CONDITION_FIELDS = frozenset(
    {
        "comparator",
        "trigger_price",
        "symbol",
        "invalidates_thesis",
        "implication_direction",
        "target_zone_low",
        "target_zone_high",
        "evidence_quote",
    }
)

_SYSTEM_PROMPT = (
    "You extract a structured trading thesis from a Traditional-Chinese "
    "market-analysis message. Only use information literally present in the "
    "message text. Never invent a symbol, price, or quote that is not "
    "verbatim in the source text. If nothing extractable is present, set "
    "confidence_status to INSUFFICIENT_DATA and leave conditions/evidence_quotes empty."
)

THESIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "primary_direction": {
            "type": "string",
            "enum": sorted(_ALLOWED_PRIMARY_DIRECTIONS),
        },
        "confidence_status": {
            "type": "string",
            "enum": sorted(_ALLOWED_CONFIDENCE_STATUSES),
        },
        "evidence_quotes": {"type": "array", "items": {"type": "string"}},
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "comparator": {"type": "string", "enum": sorted(_ALLOWED_COMPARATORS)},
                    "trigger_price": {"type": "string"},
                    "symbol": {"type": "string"},
                    "invalidates_thesis": {"type": "boolean"},
                    "implication_direction": {
                        "type": "string",
                        "enum": sorted(_ALLOWED_IMPLICATION_DIRECTIONS),
                    },
                    "target_zone_low": {"type": ["string", "null"]},
                    "target_zone_high": {"type": ["string", "null"]},
                    "evidence_quote": {"type": "string"},
                },
                "required": sorted(_CONDITION_FIELDS),
                "additionalProperties": False,
            },
        },
    },
    "required": sorted(_TOP_LEVEL_FIELDS),
    "additionalProperties": False,
}


class ThesisValidationError(ValueError):
    """The LLM response did not conform to the expected structured schema."""


@dataclass(frozen=True, slots=True)
class ParsedCondition:
    comparator: str
    trigger_price: Decimal
    symbol: str
    invalidates_thesis: bool
    implication_direction: str
    target_zone_low: Decimal | None
    target_zone_high: Decimal | None
    evidence_quote: str


@dataclass(frozen=True, slots=True)
class ParsedThesis:
    primary_direction: str
    confidence_status: str
    evidence_quotes: list[str]
    conditions: list[ParsedCondition]


def _parse_decimal(value: Any, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ThesisValidationError(f"{field} must be a decimal-as-string, got {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise ThesisValidationError(f"{field} is not a valid decimal: {value!r}") from error


def _parse_optional_decimal(value: Any, *, field: str) -> Decimal | None:
    if value is None:
        return None
    return _parse_decimal(value, field=field)


def _require_verbatim_substring(quote: str, source_text: str) -> None:
    if quote not in source_text:
        raise ThesisValidationError(
            f"evidence quote is not a verbatim substring of the source text: {quote!r}"
        )


def _parse_condition(
    raw: Any, *, valid_symbols: frozenset[str], source_text: str
) -> ParsedCondition:
    if not isinstance(raw, dict):
        raise ThesisValidationError("condition entry is not a JSON object")
    unknown = set(raw) - _CONDITION_FIELDS
    if unknown:
        raise ThesisValidationError(f"unknown condition field(s): {sorted(unknown)}")
    missing = _CONDITION_FIELDS - set(raw)
    if missing:
        raise ThesisValidationError(f"missing condition field(s): {sorted(missing)}")

    comparator = raw["comparator"]
    if comparator not in _ALLOWED_COMPARATORS:
        raise ThesisValidationError(f"invalid comparator: {comparator!r}")

    symbol = raw["symbol"]
    if symbol not in valid_symbols:
        raise ThesisValidationError(f"condition references an unresolved symbol: {symbol!r}")

    implication_direction = raw["implication_direction"]
    if implication_direction not in _ALLOWED_IMPLICATION_DIRECTIONS:
        raise ThesisValidationError(f"invalid implication_direction: {implication_direction!r}")

    invalidates_thesis = raw["invalidates_thesis"]
    if not isinstance(invalidates_thesis, bool):
        raise ThesisValidationError("invalidates_thesis must be a boolean")

    evidence_quote = raw["evidence_quote"]
    if not isinstance(evidence_quote, str):
        raise ThesisValidationError("evidence_quote must be a string")
    _require_verbatim_substring(evidence_quote, source_text)

    return ParsedCondition(
        comparator=comparator,
        trigger_price=_parse_decimal(raw["trigger_price"], field="trigger_price"),
        symbol=symbol,
        invalidates_thesis=invalidates_thesis,
        implication_direction=implication_direction,
        target_zone_low=_parse_optional_decimal(raw["target_zone_low"], field="target_zone_low"),
        target_zone_high=_parse_optional_decimal(raw["target_zone_high"], field="target_zone_high"),
        evidence_quote=evidence_quote,
    )


def parse_llm_thesis_response(
    raw: Any, *, valid_symbols: frozenset[str], source_text: str
) -> ParsedThesis:
    """Pure validating function: defense-in-depth on top of OpenAI's own strict-mode

    schema conformance (Gate 5 requires this be independently testable, not
    just trusted to the provider). Rejects unknown fields, invalid enum
    values, non-decimal prices, an invented symbol, or an evidence quote
    that is not verbatim in the source text.
    """
    if not isinstance(raw, dict):
        raise ThesisValidationError("response is not a JSON object")
    unknown = set(raw) - _TOP_LEVEL_FIELDS
    if unknown:
        raise ThesisValidationError(f"unknown top-level field(s): {sorted(unknown)}")
    missing = _TOP_LEVEL_FIELDS - set(raw)
    if missing:
        raise ThesisValidationError(f"missing required field(s): {sorted(missing)}")

    primary_direction = raw["primary_direction"]
    if primary_direction not in _ALLOWED_PRIMARY_DIRECTIONS:
        raise ThesisValidationError(f"invalid primary_direction: {primary_direction!r}")

    confidence_status = raw["confidence_status"]
    if confidence_status not in _ALLOWED_CONFIDENCE_STATUSES:
        raise ThesisValidationError(f"invalid confidence_status: {confidence_status!r}")

    evidence_quotes = raw["evidence_quotes"]
    if not isinstance(evidence_quotes, list) or not all(
        isinstance(quote, str) for quote in evidence_quotes
    ):
        raise ThesisValidationError("evidence_quotes must be a list of strings")
    for quote in evidence_quotes:
        _require_verbatim_substring(quote, source_text)

    conditions_raw = raw["conditions"]
    if not isinstance(conditions_raw, list):
        raise ThesisValidationError("conditions must be a list")
    conditions = [
        _parse_condition(entry, valid_symbols=valid_symbols, source_text=source_text)
        for entry in conditions_raw
    ]

    return ParsedThesis(
        primary_direction=primary_direction,
        confidence_status=confidence_status,
        evidence_quotes=list(evidence_quotes),
        conditions=conditions,
    )


@dataclass(frozen=True, slots=True)
class ThesisExtractionTarget:
    channel_id: int
    topic_id: int
    policy: AiContentAuthorizationSnapshot


@dataclass(frozen=True, slots=True)
class ThesisExtractionResult:
    channel_id: int
    topic_id: int
    processed_count: int
    skipped_count: int


def load_thesis_targets(session: Session) -> list[ThesisExtractionTarget]:
    """One target per `channel_policies` row passing `evaluate_channel_ai_authorization`.

    Mirrors `normalize_content.load_normalization_targets`: an unauthorized
    or non-ANALYSIS target is logged and skipped, not treated as an error.
    """
    targets: list[ThesisExtractionTarget] = []
    for row in session.scalars(select(ChannelPolicy)):
        policy = AiContentAuthorizationSnapshot(
            channel_id=row.channel_id,
            topic_id=row.topic_id,
            channel_type=row.channel_type,
            ai_authorization=row.ai_authorization,
            media_authorization=row.media_authorization,
        )
        decision = evaluate_channel_ai_authorization(policy)
        if not decision.allowed:
            LOGGER.warning(
                "channel policy excludes target from thesis extraction",
                extra={
                    "context": {
                        "channel_id": row.channel_id,
                        "topic_id": row.topic_id,
                        "reason": decision.reason,
                    }
                },
            )
            continue
        targets.append(
            ThesisExtractionTarget(channel_id=row.channel_id, topic_id=row.topic_id, policy=policy)
        )
    return targets


def _topic_condition(column: Any, topic_id: int) -> ColumnElement[bool]:
    """`channel_policies.topic_id=0` sentinel <-> a nullable topic_id column."""
    if topic_id == 0:
        return cast("ColumnElement[bool]", column.is_(None))
    return cast("ColumnElement[bool]", column == topic_id)


def _thesis_row_id(content_id: str, extraction_version: str, model: str) -> str:
    return hashlib.sha256(f"{content_id}:{extraction_version}:{model}".encode()).hexdigest()


def _thesis_id(content_id: str) -> str:
    return hashlib.sha256(content_id.encode()).hexdigest()


def _valid_symbols(resolved_symbols: list[dict[str, Any]]) -> frozenset[str]:
    return frozenset(
        entry["symbol"] for entry in resolved_symbols if entry.get("status") == "VALID"
    )


def run_thesis_extraction(
    session_factory: sessionmaker[Session],
    openai_client: Any,
    *,
    batch_size: int = 25,
    model: str,
    extraction_version: str = CURRENT_EXTRACTION_SCHEMA_VERSION,
) -> list[ThesisExtractionResult]:
    """Extract a schema-valid Thesis for each authorized, not-yet-extracted normalized_content row.

    Only `row.normalized_text` is ever sent to the LLM -- `media_sha256`/
    `media_path` are never read. `evaluate_message_ai_processing` runs
    before `openai_client.extract_structured` is ever called, so an
    unauthorized or media-blocked row makes zero provider calls. A row with
    no `VALID`-resolved symbol is skipped without a provider call (nothing
    price-conditioned to extract, and cost-conscious about paid calls). Any
    `OpenAiClientError`/`ThesisValidationError` for one row is logged and
    skipped -- no thesis row is written, so it is retried on the next run.
    """
    with session_factory() as session:
        targets = load_thesis_targets(session)

    schema_name = f"thesis_extraction_{extraction_version}"
    results: list[ThesisExtractionResult] = []
    for target in targets:
        processed_count = 0
        skipped_count = 0
        while True:
            with session_factory.begin() as session:
                checkpoint = session.get(
                    ThesisExtractionCheckpoint,
                    (target.channel_id, target.topic_id, extraction_version, model),
                )
                query = select(NormalizedContentRow).where(
                    NormalizedContentRow.channel_id == target.channel_id,
                    _topic_condition(NormalizedContentRow.topic_id, target.topic_id),
                )
                if checkpoint is not None:
                    query = query.where(
                        (NormalizedContentRow.created_at > checkpoint.last_created_at)
                        | (
                            (NormalizedContentRow.created_at == checkpoint.last_created_at)
                            & (NormalizedContentRow.content_id > checkpoint.last_content_id)
                        )
                    )
                candidates = list(
                    session.scalars(
                        query.order_by(
                            NormalizedContentRow.created_at, NormalizedContentRow.content_id
                        ).limit(batch_size)
                    )
                )
                if not candidates:
                    break

                last_created_at = (
                    checkpoint.last_created_at
                    if checkpoint is not None
                    else datetime.min.replace(tzinfo=UTC)
                )
                last_content_id = checkpoint.last_content_id if checkpoint is not None else ""
                for row in candidates:
                    last_created_at = row.created_at
                    last_content_id = row.content_id
                    valid_symbols = _valid_symbols(row.resolved_symbols)
                    if not valid_symbols:
                        skipped_count += 1
                        continue
                    decision = evaluate_message_ai_processing(
                        target.policy, media_review_status=row.media_review_status
                    )
                    if not decision.allowed:
                        LOGGER.warning(
                            "ai authorization excludes message from thesis extraction",
                            extra={
                                "context": {
                                    "content_id": row.content_id,
                                    "reason": decision.reason,
                                }
                            },
                        )
                        skipped_count += 1
                        continue
                    try:
                        raw_response = openai_client.extract_structured(
                            model=model,
                            system_prompt=_SYSTEM_PROMPT,
                            user_content=row.normalized_text,
                            json_schema=THESIS_JSON_SCHEMA,
                            schema_name=schema_name,
                        )
                        parsed = parse_llm_thesis_response(
                            raw_response,
                            valid_symbols=valid_symbols,
                            source_text=row.normalized_text,
                        )
                    except (OpenAiClientError, ThesisValidationError):
                        LOGGER.exception(
                            "thesis extraction failed for one message; skipping",
                            extra={"context": {"content_id": row.content_id}},
                        )
                        skipped_count += 1
                        continue

                    status = (
                        "INSUFFICIENT_DATA"
                        if parsed.confidence_status == "INSUFFICIENT_DATA"
                        else "DRAFT"
                    )
                    session.execute(
                        pg_insert(ThesisRow)
                        .values(
                            thesis_row_id=_thesis_row_id(row.content_id, extraction_version, model),
                            thesis_id=_thesis_id(row.content_id),
                            revision=0,
                            content_id=row.content_id,
                            channel_id=row.channel_id,
                            topic_id=row.topic_id,
                            extraction_schema_version=extraction_version,
                            llm_model=model,
                            llm_schema_name=schema_name,
                            status=status,
                            primary_direction=parsed.primary_direction,
                            confidence_status=parsed.confidence_status,
                            evidence_quotes=parsed.evidence_quotes,
                            conditions=[
                                {
                                    "comparator": condition.comparator,
                                    "trigger_price": str(condition.trigger_price),
                                    "symbol": condition.symbol,
                                    "invalidates_thesis": condition.invalidates_thesis,
                                    "implication_direction": condition.implication_direction,
                                    "target_zone_low": (
                                        str(condition.target_zone_low)
                                        if condition.target_zone_low is not None
                                        else None
                                    ),
                                    "target_zone_high": (
                                        str(condition.target_zone_high)
                                        if condition.target_zone_high is not None
                                        else None
                                    ),
                                    "evidence_quote": condition.evidence_quote,
                                }
                                for condition in parsed.conditions
                            ],
                            source_text_included=row.normalized_text,
                            content_hash=row.content_hash,
                            media_included=False,
                            raw_llm_response=raw_response,
                        )
                        .on_conflict_do_nothing(
                            index_elements=["content_id", "extraction_schema_version", "llm_model"]
                        )
                    )
                    processed_count += 1

                if checkpoint is None:
                    session.add(
                        ThesisExtractionCheckpoint(
                            channel_id=target.channel_id,
                            topic_id=target.topic_id,
                            extraction_schema_version=extraction_version,
                            llm_model=model,
                            last_created_at=last_created_at,
                            last_content_id=last_content_id,
                        )
                    )
                else:
                    checkpoint.last_created_at = last_created_at
                    checkpoint.last_content_id = last_content_id
        results.append(
            ThesisExtractionResult(
                channel_id=target.channel_id,
                topic_id=target.topic_id,
                processed_count=processed_count,
                skipped_count=skipped_count,
            )
        )
    return results
