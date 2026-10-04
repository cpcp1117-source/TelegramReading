"""Turn ANALYSIS-channel messages into stored, monitorable `analysis_setups` (AI step).

Mirrors `thesis_extraction.run_thesis_extraction` (same authorization gates, same checkpoint
idea) but differs where the analysis channels demand it: a message is read *together with the
recent messages of its topic* (an update such as "stop goes above 1.23" does not name the coin),
and a failed AI call is not skipped past -- the cursor stops there so the next run retries it
(an expired CLI login must not silently swallow the analyst's messages).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from telegram_trader.ai_authorization import evaluate_message_ai_processing
from telegram_trader.analysis_setup import (
    CURRENT_SETUP_SCHEMA_VERSION,
    SETUP_JSON_SCHEMA,
    SETUP_SYSTEM_PROMPT,
    ParsedSetup,
    SetupValidationError,
    parse_setup_response,
)
from telegram_trader.binance_market_data import load_latest_snapshot
from telegram_trader.models import (
    AnalysisSetup,
    AnalysisSetupCheckpoint,
    ChannelPolicy,
    OutboxEvent,
    TelegramMessageVersion,
)
from telegram_trader.models import NormalizedContent as NormalizedContentRow
from telegram_trader.openai_client import OpenAiClientError
from telegram_trader.outbox import append_outbox_event
from telegram_trader.thesis_extraction import ThesisExtractionTarget, load_thesis_targets

LOGGER = logging.getLogger(__name__)

SETUP_EVENT_PREFIX = "setup_"
_ACTIVE_STATUSES = ("WATCHING", "ZONE_TOUCHED")
_SYMBOL_SNAPSHOT_MAX_AGE_SECONDS = 24 * 3600.0
_CONTEXT_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class SetupExtractionResult:
    channel_id: int
    topic_id: int
    processed_count: int
    created_count: int
    skipped_count: int
    failed_count: int


def _topic_condition(column: Any, topic_id: int) -> Any:
    """`channel_policies.topic_id=0` sentinel <-> a nullable topic_id column."""
    return column.is_(None) if topic_id == 0 else column == topic_id


def setup_id_for(content_id: str, index: int, version: str, model: str) -> str:
    return hashlib.sha256(f"{content_id}:{index}:{version}:{model}".encode()).hexdigest()


def setup_event_id(setup_id: str, event_type: str, discriminator: str = "") -> str:
    return hashlib.sha256(f"{setup_id}:{event_type}:{discriminator}".encode()).hexdigest()


def _format_message(source_date: datetime, text: str) -> str:
    return f"[{source_date.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}]\n{text}"


def build_prompt(
    context: list[tuple[datetime, str]], latest: tuple[datetime, str]
) -> tuple[str, str]:
    """`(user_content, verbatim_source)`; the AI's evidence quotes must appear in the latter."""
    parts = []
    if context:
        parts.append("【較早訊息（新到舊）】")
        parts.append("\n---\n".join(_format_message(date, text) for date, text in context))
    parts.append("【最新訊息】")
    parts.append(_format_message(*latest))
    user_content = "\n\n".join(parts)
    return user_content, user_content


def _valid_symbols_for(
    session: Session, policy: ChannelPolicy, *, clock: Callable[[], datetime]
) -> frozenset[str] | None:
    """The symbols a setup from this channel may name, or `None` (fail closed) if unknown."""
    if policy.symbol_scope_mode == "STATIC_ALLOWLIST":
        return frozenset(policy.allowed_symbols)
    snapshot = load_latest_snapshot(
        session, clock=clock, max_age_seconds=_SYMBOL_SNAPSHOT_MAX_AGE_SECONDS
    )
    if snapshot is None:
        return None
    prohibited = frozenset(policy.prohibited_symbols)
    return snapshot.active_symbols - prohibited


def _context_for(
    session: Session,
    target: ThesisExtractionTarget,
    latest_date: datetime,
    latest_content_id: str,
    limit: int,
) -> list[tuple[datetime, str]]:
    if limit == 0:
        return []
    rows = session.execute(
        select(TelegramMessageVersion.source_date, NormalizedContentRow.normalized_text)
        .join(
            NormalizedContentRow,
            NormalizedContentRow.raw_message_id == TelegramMessageVersion.source_event_id,
        )
        .where(
            NormalizedContentRow.channel_id == target.channel_id,
            _topic_condition(NormalizedContentRow.topic_id, target.topic_id),
            NormalizedContentRow.content_id != latest_content_id,
            TelegramMessageVersion.source_date <= latest_date,
            TelegramMessageVersion.source_date >= latest_date - _CONTEXT_WINDOW,
            NormalizedContentRow.normalized_text != "",
        )
        .order_by(TelegramMessageVersion.source_date.desc())
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def _payload_for(setup: ParsedSetup, *, summary_zh: str, setup_id: str) -> dict[str, Any]:
    return {
        "setup_id": setup_id[:12],
        "symbol": setup.symbol,
        "side": setup.side,
        "confidence": setup.confidence,
        "summary": summary_zh,
        "entry_zone": f"{setup.entry_zone.low}-{setup.entry_zone.high}",
        "stop": str(setup.stop_value) if setup.stop_value is not None else None,
        "targets": ", ".join(f"{tp.low}-{tp.high}" for tp in setup.take_profits) or None,
        "confirm_rule": (
            f"收盤{'跌破' if setup.side == 'SHORT' else '站上'}{setup.trigger_price}"
            if setup.trigger_price is not None
            else "碰到進場區後 K 線收盤反轉確認"
        ),
        "risk_notes": "; ".join(setup.risk_notes) or None,
    }


def _store_setups(
    session: Session,
    *,
    row: NormalizedContentRow,
    source_date: datetime,
    parsed_setups: list[ParsedSetup],
    summary_zh: str,
    raw_response: dict[str, Any],
    model: str,
    expiry_hours: int,
    now: datetime,
) -> int:
    created = 0
    for index, setup in enumerate(parsed_setups):
        setup_id = setup_id_for(row.content_id, index, CURRENT_SETUP_SCHEMA_VERSION, model)
        if session.get(AnalysisSetup, setup_id) is not None:
            continue
        # A newer idea for the same symbol/side in this topic replaces the older one; an idea
        # older than one already being watched is stored closed and never announced.
        same_idea = (
            AnalysisSetup.channel_id == row.channel_id,
            _topic_condition(AnalysisSetup.topic_id, row.topic_id or 0),
            AnalysisSetup.symbol == setup.symbol,
            AnalysisSetup.side == setup.side,
            AnalysisSetup.status.in_(_ACTIVE_STATUSES),
        )
        has_newer_active = (
            session.scalar(
                select(AnalysisSetup.setup_id)
                .where(*same_idea, AnalysisSetup.valid_from > source_date)
                .limit(1)
            )
            is not None
        )
        if not has_newer_active:
            session.execute(
                update(AnalysisSetup)
                .where(*same_idea, AnalysisSetup.valid_from <= source_date)
                .values(status="SUPERSEDED", status_reason="NEWER_SETUP", last_checked_at=now)
            )
        session.add(
            AnalysisSetup(
                setup_id=setup_id,
                content_id=row.content_id,
                raw_message_id=row.raw_message_id,
                channel_id=row.channel_id,
                topic_id=row.topic_id,
                extraction_schema_version=CURRENT_SETUP_SCHEMA_VERSION,
                llm_model=model,
                symbol=setup.symbol,
                side=setup.side,
                entry_zone_low=setup.entry_zone.low,
                entry_zone_high=setup.entry_zone.high,
                confirm_rule=setup.confirm_rule,
                trigger_price=setup.trigger_price,
                stop_value=setup.stop_value,
                take_profits=[
                    {"low": str(tp.low), "high": str(tp.high)} for tp in setup.take_profits
                ],
                cancel_if_target_first=setup.cancel_if_target_first,
                confidence=setup.confidence,
                summary_zh=summary_zh,
                risk_notes=setup.risk_notes,
                evidence_quotes=setup.evidence_quotes,
                status="SUPERSEDED" if has_newer_active else "WATCHING",
                status_reason="OLDER_THAN_ACTIVE" if has_newer_active else None,
                valid_from=source_date,
                expires_at=source_date + timedelta(hours=expiry_hours),
                raw_llm_response=raw_response,
            )
        )
        session.flush()
        if has_newer_active:
            continue
        append_outbox_event(
            session,
            event_id=setup_event_id(setup_id, "setup_created"),
            event_type="setup_created",
            aggregate_type="analysis_setup",
            aggregate_id=setup_id,
            payload=_payload_for(setup, summary_zh=summary_zh, setup_id=setup_id),
        )
        created += 1
    return created


def _notify_failure(
    session: Session, row: NormalizedContentRow, reason: str, now: datetime
) -> None:
    """One failure notice per message (deterministic event id), never one per retry."""
    event_id = setup_event_id(row.content_id, "setup_extraction_failed")
    if session.get(OutboxEvent, event_id) is not None:
        return
    append_outbox_event(
        session,
        event_id=event_id,
        event_type="setup_extraction_failed",
        aggregate_type="normalized_content",
        aggregate_id=row.content_id,
        payload={"reason": reason[:300], "failed_at": now.isoformat()},
    )


def run_setup_extraction(
    session_factory: sessionmaker[Session],
    ai_client: Any,
    *,
    model: str,
    batch_size: int = 10,
    context_message_count: int = 8,
    expiry_hours: int = 48,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[SetupExtractionResult]:
    with session_factory() as session:
        targets = load_thesis_targets(session)

    version = CURRENT_SETUP_SCHEMA_VERSION
    schema_name = f"analysis_setup_{version}"
    results: list[SetupExtractionResult] = []
    for target in targets:
        processed = created = skipped = failed = 0
        stop_target = False
        while not stop_target:
            now = clock()
            with session_factory.begin() as session:
                policy = session.get(ChannelPolicy, (target.channel_id, target.topic_id))
                valid_symbols = (
                    _valid_symbols_for(session, policy, clock=clock) if policy is not None else None
                )
                if valid_symbols is None:
                    LOGGER.warning(
                        "no symbol universe for target; skipping setup extraction",
                        extra={"context": {"channel_id": target.channel_id}},
                    )
                    break
                checkpoint = session.get(
                    AnalysisSetupCheckpoint,
                    (target.channel_id, target.topic_id, version, model),
                )
                query = select(NormalizedContentRow, TelegramMessageVersion.source_date).join(
                    TelegramMessageVersion,
                    NormalizedContentRow.raw_message_id == TelegramMessageVersion.source_event_id,
                )
                query = query.where(
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
                candidates = session.execute(
                    query.order_by(
                        NormalizedContentRow.created_at, NormalizedContentRow.content_id
                    ).limit(batch_size)
                ).all()
                if not candidates:
                    break

                last_created_at = (
                    checkpoint.last_created_at
                    if checkpoint is not None
                    else datetime.min.replace(tzinfo=UTC)
                )
                last_content_id = checkpoint.last_content_id if checkpoint is not None else ""
                # Messages normalized in one batch share a `created_at`, so the cursor order says
                # nothing about the analyst's timeline: process oldest-first so an update is
                # applied after the idea it updates, and only move the cursor over a message once
                # it (and everything before it in cursor order) is done.
                done: set[str] = set()
                for row, source_date in sorted(candidates, key=lambda c: (c[1], c[0].content_id)):
                    if not row.normalized_text.strip():
                        skipped += 1
                    elif source_date + timedelta(hours=expiry_hours) <= now:
                        skipped += 1  # too old to still be actionable: no AI call at all
                    elif not evaluate_message_ai_processing(
                        target.policy, media_review_status=row.media_review_status
                    ).allowed:
                        skipped += 1
                    else:
                        context = _context_for(
                            session, target, source_date, row.content_id, context_message_count
                        )
                        user_content, source_text = build_prompt(
                            context, (source_date, row.normalized_text)
                        )
                        try:
                            raw_response = ai_client.extract_structured(
                                model=model,
                                system_prompt=SETUP_SYSTEM_PROMPT,
                                user_content=user_content,
                                json_schema=SETUP_JSON_SCHEMA,
                                schema_name=schema_name,
                            )
                        except OpenAiClientError as error:
                            LOGGER.exception(
                                "setup extraction AI call failed; will retry next run",
                                extra={"context": {"content_id": row.content_id}},
                            )
                            _notify_failure(session, row, str(error), now)
                            failed += 1
                            stop_target = True
                            break  # do not advance the cursor past an unprocessed message
                        try:
                            parsed = parse_setup_response(
                                raw_response, valid_symbols=valid_symbols, source_text=source_text
                            )
                        except SetupValidationError:
                            LOGGER.exception(
                                "setup extraction response invalid; skipping message",
                                extra={"context": {"content_id": row.content_id}},
                            )
                            skipped += 1
                        else:
                            for reason in parsed.rejected:
                                LOGGER.warning(
                                    "setup rejected by validation",
                                    extra={
                                        "context": {"content_id": row.content_id, "reason": reason}
                                    },
                                )
                            created += _store_setups(
                                session,
                                row=row,
                                source_date=source_date,
                                parsed_setups=parsed.setups,
                                summary_zh=parsed.summary_zh,
                                raw_response=raw_response,
                                model=model,
                                expiry_hours=expiry_hours,
                                now=now,
                            )
                            processed += 1
                    done.add(row.content_id)

                for row, _ in candidates:
                    if row.content_id not in done:
                        break
                    last_created_at = row.created_at
                    last_content_id = row.content_id

                if last_content_id:
                    if checkpoint is None:
                        session.add(
                            AnalysisSetupCheckpoint(
                                channel_id=target.channel_id,
                                topic_id=target.topic_id,
                                extraction_schema_version=version,
                                llm_model=model,
                                last_created_at=last_created_at,
                                last_content_id=last_content_id,
                            )
                        )
                    else:
                        checkpoint.last_created_at = last_created_at
                        checkpoint.last_content_id = last_content_id
        results.append(
            SetupExtractionResult(
                channel_id=target.channel_id,
                topic_id=target.topic_id,
                processed_count=processed,
                created_count=created,
                skipped_count=skipped,
                failed_count=failed,
            )
        )
    return results
