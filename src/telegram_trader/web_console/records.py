"""Read-only queries behind the Web Console record pages.

Every function takes an open Session and returns plain JSON-ready dicts:
Decimals become strings (no float rounding of prices/quantities) and
datetimes become ISO-8601 strings, which the browser renders in local time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.orm import Session, aliased

from telegram_trader.models import (
    AuditEvent,
    ChannelPolicy,
    ExchangeOrder,
    NormalizedSignal,
    OrderLifecycle,
    ProtectionOrder,
    RiskDecision,
    SignalDecisionEdit,
    SignalDecisionEvent,
    SignalDecisionRequest,
    TakeProfitOrder,
    TelegramMessageVersion,
    Thesis,
    TradeIntent,
)

MAX_PAGE_SIZE = 200
# A position is open from the entry fill until the position monitor writes CLOSED.
OPEN_POSITION_STATES = ("FILLED", "PROTECTION_PENDING", "PROTECTED", "PROTECTION_FAILED")
REJECTED_OUTCOMES = ("REJECTED", "REJECTED_STALE", "REJECTED_EXPIRED")

MarkPriceFetcher = Callable[[str], Decimal | None]


@dataclass(frozen=True)
class Page:
    limit: int = 50
    offset: int = 0

    def clamp(self) -> Page:
        return Page(limit=max(1, min(self.limit, MAX_PAGE_SIZE)), offset=max(0, self.offset))


def to_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: to_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    return value


def _row(obj: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {name: to_json(getattr(obj, name)) for name in fields}


def _paged(session: Session, stmt: Select[Any], page: Page) -> tuple[list[Any], int]:
    page = page.clamp()
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(session.execute(stmt.limit(page.limit).offset(page.offset)).all())
    return rows, total


def channel_labels(session: Session) -> dict[tuple[int, int], str]:
    """`(channel_id, topic_id or 0)` -> label, the channel_policies sentinel convention."""
    return {
        (policy.channel_id, policy.topic_id): policy.label
        for policy in session.scalars(select(ChannelPolicy))
    }


def _label(labels: dict[tuple[int, int], str], channel_id: int | None, topic_id: int | None) -> str:
    if channel_id is None:
        return ""
    return labels.get((channel_id, topic_id or 0)) or labels.get((channel_id, 0)) or str(channel_id)


def list_channels(session: Session) -> list[dict[str, Any]]:
    return [
        {
            "channel_id": policy.channel_id,
            "topic_id": policy.topic_id,
            "label": policy.label,
            "channel_type": policy.channel_type,
        }
        for policy in session.scalars(
            select(ChannelPolicy).order_by(ChannelPolicy.channel_id, ChannelPolicy.topic_id)
        )
    ]


# ---------------------------------------------------------------- signals


def parser_versions(session: Session) -> list[str]:
    """Newest first, by when each version first produced a row."""
    stmt = (
        select(NormalizedSignal.parser_version)
        .group_by(NormalizedSignal.parser_version)
        .order_by(func.min(NormalizedSignal.created_at).desc())
    )
    return list(session.scalars(stmt))


@dataclass(frozen=True)
class SignalFilter:
    parser_version: str | None = None
    status: str | None = None
    symbol: str | None = None
    channel_id: int | None = None
    decision: str | None = None  # approved | rejected | pending | not_notified


def list_signals(session: Session, flt: SignalFilter, page: Page) -> dict[str, Any]:
    last_event = (
        select(SignalDecisionEvent.outcome, SignalDecisionEvent.decided_at)
        .where(SignalDecisionEvent.request_id == SignalDecisionRequest.request_id)
        .order_by(SignalDecisionEvent.decided_at.desc())
        .limit(1)
        .correlate(SignalDecisionRequest)
    )
    outcome = last_event.with_only_columns(SignalDecisionEvent.outcome).scalar_subquery()
    decided_at = last_event.with_only_columns(SignalDecisionEvent.decided_at).scalar_subquery()
    stmt = (
        select(
            NormalizedSignal,
            TelegramMessageVersion.source_date,
            SignalDecisionRequest.request_id,
            SignalDecisionRequest.requested_at,
            outcome.label("outcome"),
            decided_at.label("decided_at"),
        )
        .join(
            TelegramMessageVersion,
            TelegramMessageVersion.source_event_id == NormalizedSignal.raw_message_id,
        )
        .outerjoin(
            SignalDecisionRequest,
            SignalDecisionRequest.signal_row_id == NormalizedSignal.signal_row_id,
        )
        # When the author posted it -- `created_at` is only when a parser batch ran.
        .order_by(TelegramMessageVersion.source_date.desc(), NormalizedSignal.signal_row_id)
    )
    conditions: list[ColumnElement[bool]] = []
    if flt.parser_version:
        conditions.append(NormalizedSignal.parser_version == flt.parser_version)
    if flt.status:
        conditions.append(NormalizedSignal.status == flt.status)
    if flt.symbol:
        conditions.append(NormalizedSignal.symbol.ilike(f"%{flt.symbol.strip()}%"))
    if flt.channel_id is not None:
        conditions.append(NormalizedSignal.channel_id == flt.channel_id)
    if flt.decision == "approved":
        conditions.append(outcome == "APPROVED")
    elif flt.decision == "rejected":
        conditions.append(outcome.in_(REJECTED_OUTCOMES))
    elif flt.decision == "pending":
        conditions.append(and_(SignalDecisionRequest.request_id.is_not(None), outcome.is_(None)))
    elif flt.decision == "not_notified":
        conditions.append(SignalDecisionRequest.request_id.is_(None))
    if conditions:
        stmt = stmt.where(*conditions)

    rows, total = _paged(session, stmt, page)
    labels = channel_labels(session)
    items = []
    for signal, source_date, request_id, requested_at, last_outcome, last_decided_at in rows:
        item = _row(
            signal,
            (
                "signal_row_id",
                "signal_id",
                "revision",
                "status",
                "symbol",
                "side",
                "entry_type",
                "entry_values",
                "stop_value",
                "stop_origin",
                "take_profits",
                "parser_version",
                "expires_at",
                "created_at",
            ),
        )
        item["channel"] = _label(labels, signal.channel_id, signal.topic_id)
        item["source_date"] = to_json(source_date)
        item["request_id"] = request_id
        item["requested_at"] = to_json(requested_at)
        item["outcome"] = last_outcome
        item["decided_at"] = to_json(last_decided_at)
        items.append(item)
    return {"items": items, "total": total}


def signal_detail(session: Session, signal_row_id: str) -> dict[str, Any] | None:
    signal = session.get(NormalizedSignal, signal_row_id)
    if signal is None:
        return None
    labels = channel_labels(session)
    detail = _row(
        signal,
        (
            "signal_row_id",
            "signal_id",
            "revision",
            "status",
            "symbol",
            "side",
            "entry_type",
            "entry_values",
            "stop_value",
            "stop_origin",
            "take_profits",
            "evidence_spans",
            "link_method",
            "parser_version",
            "expires_at",
            "created_at",
        ),
    )
    detail["channel"] = _label(labels, signal.channel_id, signal.topic_id)
    message = session.scalar(
        select(TelegramMessageVersion).where(
            TelegramMessageVersion.source_event_id == signal.raw_message_id
        )
    )
    detail["message"] = (
        None
        if message is None
        else _row(message, ("message_id", "source_date", "received_at", "text"))
    )
    request = session.scalar(
        select(SignalDecisionRequest).where(SignalDecisionRequest.signal_row_id == signal_row_id)
    )
    detail["request"] = (
        None if request is None else _row(request, ("request_id", "requested_at", "expires_at"))
    )
    detail["edits"] = []
    detail["decisions"] = []
    detail["intent_ids"] = []
    if request is not None:
        detail["edits"] = [
            _row(edit, ("revision", "stop_value", "take_profits", "edited_at"))
            for edit in session.scalars(
                select(SignalDecisionEdit)
                .where(SignalDecisionEdit.request_id == request.request_id)
                .order_by(SignalDecisionEdit.revision)
            )
        ]
        detail["decisions"] = [
            _row(
                event,
                (
                    "outcome",
                    "actor_user_id",
                    "approved_stop_value",
                    "approved_take_profits",
                    "decided_at",
                ),
            )
            for event in session.scalars(
                select(SignalDecisionEvent)
                .where(SignalDecisionEvent.request_id == request.request_id)
                .order_by(SignalDecisionEvent.decided_at)
            )
        ]
    detail["intent_ids"] = list(
        session.scalars(
            select(TradeIntent.intent_id).where(TradeIntent.signal_row_id == signal_row_id)
        )
    )
    # Every revision of the same trading idea, so a follow-up edit/cancel is visible.
    detail["revisions"] = [
        _row(rev, ("signal_row_id", "revision", "status", "created_at"))
        for rev in session.scalars(
            select(NormalizedSignal)
            .where(
                NormalizedSignal.signal_id == signal.signal_id,
                NormalizedSignal.parser_version == signal.parser_version,
            )
            .order_by(NormalizedSignal.revision)
        )
    ]
    return detail


# ---------------------------------------------------------------- orders


def _latest_lifecycle() -> Any:
    latest = (
        select(OrderLifecycle.intent_id, func.max(OrderLifecycle.revision).label("revision"))
        .group_by(OrderLifecycle.intent_id)
        .subquery()
    )
    lifecycle = aliased(OrderLifecycle)
    join_on = and_(
        lifecycle.intent_id == latest.c.intent_id, lifecycle.revision == latest.c.revision
    )
    return latest, lifecycle, join_on


@dataclass(frozen=True)
class OrderFilter:
    symbol: str | None = None
    verdict: str | None = None
    state: str | None = None


def list_orders(session: Session, flt: OrderFilter, page: Page) -> dict[str, Any]:
    latest, lifecycle, join_on = _latest_lifecycle()
    entry = aliased(ExchangeOrder)
    stmt = (
        select(TradeIntent, RiskDecision, lifecycle.state, lifecycle.transitioned_at, entry)
        .outerjoin(RiskDecision, RiskDecision.intent_id == TradeIntent.intent_id)
        .outerjoin(latest, latest.c.intent_id == TradeIntent.intent_id)
        .outerjoin(lifecycle, join_on)
        .outerjoin(entry, and_(entry.intent_id == TradeIntent.intent_id, entry.purpose == "ENTRY"))
        .order_by(TradeIntent.created_at.desc(), TradeIntent.intent_id)
    )
    if flt.symbol:
        stmt = stmt.where(TradeIntent.symbol.ilike(f"%{flt.symbol.strip()}%"))
    if flt.verdict:
        stmt = stmt.where(RiskDecision.verdict == flt.verdict)
    if flt.state == "NONE":
        stmt = stmt.where(lifecycle.state.is_(None))
    elif flt.state:
        stmt = stmt.where(lifecycle.state == flt.state)

    rows, total = _paged(session, stmt, page)
    labels = channel_labels(session)
    items = []
    for intent, decision, state, state_at, entry_order in rows:
        item = _row(
            intent,
            (
                "intent_id",
                "symbol",
                "side",
                "entry_type",
                "stop_value",
                "take_profits",
                "created_at",
            ),
        )
        item["channel"] = _label(labels, intent.channel_id, None)
        item["verdict"] = decision.verdict if decision else None
        item["reason_codes"] = decision.reason_codes if decision else []
        item["quantity"] = to_json(decision.quantity) if decision else None
        item["state"] = state
        item["state_at"] = to_json(state_at)
        item["entry_status"] = entry_order.status if entry_order else None
        item["avg_fill_price"] = to_json(entry_order.avg_fill_price) if entry_order else None
        items.append(item)
    return {"items": items, "total": total}


_EXCHANGE_ORDER_FIELDS = (
    "purpose",
    "client_order_id",
    "exchange_order_id",
    "side",
    "order_type",
    "reduce_only",
    "close_position",
    "working_type",
    "requested_quantity",
    "requested_price",
    "requested_stop_price",
    "status",
    "filled_quantity",
    "avg_fill_price",
    "submitted_at",
    "last_checked_at",
    "raw_response",
)


def order_detail(session: Session, intent_id: str) -> dict[str, Any] | None:
    intent = session.get(TradeIntent, intent_id)
    if intent is None:
        return None
    labels = channel_labels(session)
    detail = _row(
        intent,
        (
            "intent_id",
            "signal_row_id",
            "symbol",
            "side",
            "entry_type",
            "entry_values",
            "stop_value",
            "take_profits",
            "status",
            "created_at",
        ),
    )
    detail["channel"] = _label(labels, intent.channel_id, None)
    decision = session.scalar(select(RiskDecision).where(RiskDecision.intent_id == intent_id))
    detail["risk_decision"] = (
        None
        if decision is None
        else _row(
            decision,
            (
                "verdict",
                "reason_codes",
                "computed_stop_price",
                "quantity",
                "entry_price_used",
                "market_price",
                "market_price_fetched_at",
                "equity_used",
                "open_position_count_used",
                "daily_realized_loss_pct_used",
                "config_snapshot_id",
                "expires_at",
                "created_at",
            ),
        )
    )
    detail["lifecycle"] = [
        _row(row, ("revision", "state", "transition_reason", "transitioned_at"))
        for row in session.scalars(
            select(OrderLifecycle)
            .where(OrderLifecycle.intent_id == intent_id)
            .order_by(OrderLifecycle.revision)
        )
    ]
    detail["exchange_orders"] = [
        _row(row, _EXCHANGE_ORDER_FIELDS)
        for row in session.scalars(
            select(ExchangeOrder)
            .where(ExchangeOrder.intent_id == intent_id)
            .order_by(ExchangeOrder.created_at)
        )
    ]
    protection = session.scalar(
        select(ProtectionOrder).where(ProtectionOrder.intent_id == intent_id)
    )
    detail["protection"] = (
        None
        if protection is None
        else _row(protection, ("protected_quantity", "stop_price", "state", "confirmed_at"))
    )
    take_profit = session.scalar(
        select(TakeProfitOrder).where(TakeProfitOrder.intent_id == intent_id)
    )
    detail["take_profit"] = (
        None
        if take_profit is None
        else _row(take_profit, ("target_quantity", "target_price", "state", "confirmed_at"))
    )
    detail["events"] = [
        _row(event, ("event_id", "event_type", "payload", "recorded_at"))
        for event in session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.aggregate_type == "trade_intent", AuditEvent.aggregate_id == intent_id
            )
            .order_by(AuditEvent.recorded_at)
        )
    ]
    return detail


# ---------------------------------------------------------------- positions


def list_open_positions(session: Session, mark_price: MarkPriceFetcher) -> list[dict[str, Any]]:
    latest, lifecycle, join_on = _latest_lifecycle()
    stmt = (
        select(lifecycle)
        .join(latest, join_on)
        .where(lifecycle.state.in_(OPEN_POSITION_STATES))
        .order_by(lifecycle.transitioned_at)
    )
    labels = channel_labels(session)
    prices: dict[str, Decimal | None] = {}
    items = []
    for row in session.scalars(stmt):
        intent = session.get(TradeIntent, row.intent_id)
        entry_order = session.scalar(
            select(ExchangeOrder).where(
                ExchangeOrder.intent_id == row.intent_id, ExchangeOrder.purpose == "ENTRY"
            )
        )
        protection = session.scalar(
            select(ProtectionOrder).where(ProtectionOrder.intent_id == row.intent_id)
        )
        take_profit = session.scalar(
            select(TakeProfitOrder).where(TakeProfitOrder.intent_id == row.intent_id)
        )
        opened_at = session.scalar(
            select(OrderLifecycle.transitioned_at).where(
                OrderLifecycle.intent_id == row.intent_id, OrderLifecycle.state == "FILLED"
            )
        )
        quantity = entry_order.filled_quantity if entry_order else None
        entry_price = entry_order.avg_fill_price if entry_order else None
        if row.symbol not in prices:
            prices[row.symbol] = mark_price(row.symbol)
        mark = prices[row.symbol]
        unrealized = None
        if mark is not None and entry_price is not None and quantity:
            direction = Decimal(1) if row.side == "LONG" else Decimal(-1)
            unrealized = (mark - entry_price) * quantity * direction
        items.append(
            {
                "intent_id": row.intent_id,
                "symbol": row.symbol,
                "side": row.side,
                "state": row.state,
                "channel": _label(labels, intent.channel_id if intent else None, None),
                "quantity": to_json(quantity),
                "entry_price": to_json(entry_price),
                "stop_price": to_json(protection.stop_price) if protection else None,
                "stop_state": protection.state if protection else None,
                "take_profit_price": to_json(take_profit.target_price) if take_profit else None,
                "take_profit_state": take_profit.state if take_profit else None,
                "mark_price": to_json(mark),
                "unrealized_pnl": to_json(unrealized.quantize(Decimal("0.0001")))
                if unrealized is not None
                else None,
                "opened_at": to_json(opened_at or row.transitioned_at),
            }
        )
    return items


# ---------------------------------------------------------------- theses


@dataclass(frozen=True)
class ThesisFilter:
    channel_id: int | None = None
    direction: str | None = None
    status: str | None = None


def list_theses(session: Session, flt: ThesisFilter, page: Page) -> dict[str, Any]:
    stmt = select(Thesis).order_by(Thesis.created_at.desc(), Thesis.thesis_row_id)
    if flt.channel_id is not None:
        stmt = stmt.where(Thesis.channel_id == flt.channel_id)
    if flt.direction:
        stmt = stmt.where(Thesis.primary_direction == flt.direction)
    if flt.status:
        stmt = stmt.where(Thesis.status == flt.status)
    rows, total = _paged(session, stmt, page)
    labels = channel_labels(session)
    items = []
    for (thesis,) in rows:
        item = _row(
            thesis,
            (
                "thesis_row_id",
                "status",
                "primary_direction",
                "confidence_status",
                "conditions",
                "llm_model",
                "created_at",
            ),
        )
        item["channel"] = _label(labels, thesis.channel_id, thesis.topic_id)
        item["excerpt"] = thesis.source_text_included[:80]
        items.append(item)
    return {"items": items, "total": total}


def thesis_detail(session: Session, thesis_row_id: str) -> dict[str, Any] | None:
    thesis = session.get(Thesis, thesis_row_id)
    if thesis is None:
        return None
    detail = _row(
        thesis,
        (
            "thesis_row_id",
            "thesis_id",
            "status",
            "primary_direction",
            "confidence_status",
            "evidence_quotes",
            "conditions",
            "source_text_included",
            "media_included",
            "extraction_schema_version",
            "llm_model",
            "created_at",
        ),
    )
    detail["channel"] = _label(channel_labels(session), thesis.channel_id, thesis.topic_id)
    return detail


# ---------------------------------------------------------------- audit


@dataclass(frozen=True)
class AuditFilter:
    event_type: str | None = None
    aggregate_type: str | None = None
    aggregate_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


def audit_facets(session: Session) -> dict[str, list[str]]:
    return {
        "event_types": list(
            session.scalars(
                select(AuditEvent.event_type)
                .group_by(AuditEvent.event_type)
                .order_by(AuditEvent.event_type)
            )
        ),
        "aggregate_types": list(
            session.scalars(
                select(AuditEvent.aggregate_type)
                .group_by(AuditEvent.aggregate_type)
                .order_by(AuditEvent.aggregate_type)
            )
        ),
    }


def list_audit(session: Session, flt: AuditFilter, page: Page) -> dict[str, Any]:
    stmt = select(AuditEvent).order_by(AuditEvent.recorded_at.desc(), AuditEvent.event_id)
    if flt.event_type:
        stmt = stmt.where(AuditEvent.event_type == flt.event_type)
    if flt.aggregate_type:
        stmt = stmt.where(AuditEvent.aggregate_type == flt.aggregate_type)
    if flt.aggregate_id:
        stmt = stmt.where(AuditEvent.aggregate_id == flt.aggregate_id.strip())
    if flt.since is not None:
        stmt = stmt.where(AuditEvent.recorded_at >= flt.since)
    if flt.until is not None:
        stmt = stmt.where(AuditEvent.recorded_at < flt.until)
    rows, total = _paged(session, stmt, page)
    return {
        "items": [
            _row(
                event,
                (
                    "event_id",
                    "event_type",
                    "aggregate_type",
                    "aggregate_id",
                    "payload",
                    "recorded_at",
                ),
            )
            for (event,) in rows
        ],
        "total": total,
    }
