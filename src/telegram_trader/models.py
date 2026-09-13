from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_aggregate", "aggregate_type", "aggregate_id", "recorded_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_recorded_at", "recorded_at"),)

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxDeliveryReceipt(Base):
    __tablename__ = "outbox_delivery_receipts"

    consumer_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("outbox_events.event_id"), primary_key=True
    )
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConsumerCheckpoint(Base):
    __tablename__ = "consumer_checkpoints"
    __table_args__ = (CheckConstraint("last_sequence >= 0", name="ck_checkpoint_non_negative"),)

    consumer_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MockMessageReceipt(Base):
    __tablename__ = "mock_message_receipts"
    __table_args__ = (
        CheckConstraint("message_id > 0", name="ck_mock_message_id_positive"),
        CheckConstraint("edit_version >= 0", name="ck_mock_edit_version_non_negative"),
        CheckConstraint(
            "reply_to_message_id IS NULL OR reply_to_message_id > 0",
            name="ck_mock_reply_message_id_positive",
        ),
        CheckConstraint("sequence_no > 0", name="ck_mock_sequence_positive"),
        UniqueConstraint("consumer_name", "sequence_no", name="uq_mock_receipt_consumer_sequence"),
        UniqueConstraint(
            "channel_id", "message_id", "edit_version", name="uq_mock_receipt_message_version"
        ),
    )

    source_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    edit_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("audit_events.event_id"), nullable=False, unique=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TelegramCollectorCheckpoint(Base):
    """Checkpoint identity is (channel_id, topic_id). `topic_id=0` is the sentinel for

    "whole channel, no forum topic" — Postgres primary key columns cannot be NULL, so
    this table cannot reuse the nullable-NULL convention used by `TelegramMessageVersion.topic_id`.
    """

    __tablename__ = "telegram_collector_checkpoints"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_telegram_checkpoint_channel_positive"),
        CheckConstraint("topic_id >= 0", name="ck_telegram_checkpoint_topic_non_negative"),
        CheckConstraint("last_message_id >= 0", name="ck_telegram_checkpoint_message_non_negative"),
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default="0")
    last_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TelegramMessageVersion(Base):
    __tablename__ = "telegram_message_versions"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_telegram_message_channel_positive"),
        CheckConstraint("message_id > 0", name="ck_telegram_message_id_positive"),
        CheckConstraint("edit_version >= 0", name="ck_telegram_edit_version_non_negative"),
        CheckConstraint(
            "reply_to_message_id IS NULL OR reply_to_message_id > 0",
            name="ck_telegram_reply_message_positive",
        ),
        CheckConstraint(
            "forward_origin_id IS NULL OR forward_origin_id > 0",
            name="ck_telegram_forward_origin_positive",
        ),
        CheckConstraint(
            "forward_message_id IS NULL OR forward_message_id > 0",
            name="ck_telegram_forward_message_positive",
        ),
        CheckConstraint(
            "media_size_bytes IS NULL OR media_size_bytes >= 0",
            name="ck_telegram_media_size_non_negative",
        ),
        CheckConstraint(
            "topic_id IS NULL OR topic_id > 0",
            name="ck_telegram_topic_positive",
        ),
        UniqueConstraint(
            "channel_id",
            "message_id",
            "edit_version",
            name="uq_telegram_message_version",
        ),
        Index(
            "ix_telegram_message_channel_message",
            "channel_id",
            "message_id",
            "edit_version",
        ),
    )

    source_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    edit_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    is_backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    forward_origin_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    forward_origin_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    forward_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    forward_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    media_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    media_mime_type: Mapped[str | None] = mapped_column(String(150), nullable=True)
    media_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("audit_events.event_id"), nullable=False, unique=True
    )


class ChannelPolicy(Base):
    """Enforced Channel Policy per (channel_id, topic_id); `topic_id=0` is the

    "whole channel" sentinel, same convention as `TelegramCollectorCheckpoint`.
    This table is the runtime source of truth; the per-channel onboarding
    markdown under docs/phase-0/channels/ is the human-readable justification
    and evidence record and must be corrected to match if the two disagree.
    """

    __tablename__ = "channel_policies"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_channel_policy_channel_positive"),
        CheckConstraint("topic_id >= 0", name="ck_channel_policy_topic_non_negative"),
        CheckConstraint(
            "channel_type IN ('ANALYSIS', 'EXECUTION_SIGNAL')",
            name="ck_channel_policy_type_valid",
        ),
        CheckConstraint(
            "access_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_access_auth_valid",
        ),
        CheckConstraint(
            "automation_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_automation_auth_valid",
        ),
        CheckConstraint(
            "ai_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_ai_auth_valid",
        ),
        CheckConstraint(
            "media_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_media_auth_valid",
        ),
        CheckConstraint(
            "symbol_scope_mode IN ('STATIC_ALLOWLIST', 'BINANCE_USDM_ACTIVE_PERPETUAL')",
            name="ck_channel_policy_symbol_scope_valid",
        ),
        CheckConstraint(
            "gate_decision IN ('MONITOR_ONLY', 'ENABLED', 'PAUSED', 'REJECTED')",
            name="ck_channel_policy_gate_decision_valid",
        ),
        CheckConstraint("raw_retention_days > 0", name="ck_channel_policy_retention_positive"),
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default="0")
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    access_authorization: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="UNKNOWN"
    )
    automation_authorization: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="UNKNOWN"
    )
    ai_authorization: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="UNKNOWN"
    )
    media_authorization: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="UNKNOWN"
    )
    symbol_scope_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    allowed_symbols: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    prohibited_symbols: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    message_languages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    supported_content_types: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    raw_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    gate_decision: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    onboarding_doc_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    acceptance_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acceptance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NormalizedContent(Base):
    """Deterministic normalization of one `telegram_message_versions` row (FR-007).

    `content_id` is derived from `(raw_message_id, normalizer_version)`, so
    re-normalizing the same message with the same version is naturally
    idempotent, and bumping `normalizer_version` appends a new row instead of
    overwriting the old one -- the same "never silently overwrite" convention
    as edit versions on the raw table. `ON DELETE CASCADE` means retention
    cleanup deleting an expired raw row removes its normalized derivative
    too; no separate retention job is needed for this table.
    """

    __tablename__ = "normalized_content"
    __table_args__ = (
        CheckConstraint(
            "symbol_scope_mode IN ('STATIC_ALLOWLIST', 'BINANCE_USDM_ACTIVE_PERPETUAL')",
            name="ck_normalized_content_symbol_scope_valid",
        ),
        CheckConstraint(
            "media_review_status IN ('NOT_APPLICABLE', 'PENDING_MANUAL_REVIEW')",
            name="ck_normalized_content_media_review_valid",
        ),
        UniqueConstraint(
            "raw_message_id",
            "normalizer_version",
            name="uq_normalized_content_message_version",
        ),
        Index("ix_normalized_content_channel_topic", "channel_id", "topic_id"),
    )

    content_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    raw_message_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("telegram_message_versions.source_event_id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    normalizer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    symbol_scope_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol_candidates: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    resolved_symbols: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    media_review_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="NOT_APPLICABLE"
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NormalizedSignal(Base):
    """One immutable revision of a parsed EXECUTION_SIGNAL lifecycle (FR-009/FR-010/FR-011).

    `signal_row_id` is this row's own identity, derived from
    `(raw_message_id, parser_version)` -- idempotent re-parsing, and a
    `parser_version` bump appends fresh rows rather than overwriting old
    ones (same convention as `NormalizedContent`). `signal_id` is the
    separate, stable *aggregate* identity shared across every revision of
    "the same trading idea": revision 0 derives it from its own
    `raw_message_id`; a reply-linked follow-up inherits its parent's
    `signal_id` and increments `revision`. The current state of a signal is
    whichever row has the highest `revision` for that `signal_id` -- older
    revisions are never updated or deleted except via the same
    `ON DELETE CASCADE` retention path as `NormalizedContent`.
    """

    __tablename__ = "normalized_signals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('NEW', 'INCOMPLETE', 'VALIDATED', 'CANCELLED', 'EXPIRED', 'SUPERSEDED')",
            name="ck_normalized_signal_status_valid",
        ),
        CheckConstraint(
            "side IS NULL OR side IN ('LONG', 'SHORT')",
            name="ck_normalized_signal_side_valid",
        ),
        CheckConstraint(
            "entry_type IS NULL OR entry_type IN ('MARKET', 'LIMIT', 'RANGE')",
            name="ck_normalized_signal_entry_type_valid",
        ),
        CheckConstraint(
            "stop_origin IN ('AUTHOR', 'DEFAULT_ROE_30', 'NONE')",
            name="ck_normalized_signal_stop_origin_valid",
        ),
        CheckConstraint("revision >= 0", name="ck_normalized_signal_revision_non_negative"),
        UniqueConstraint(
            "signal_id",
            "revision",
            "parser_version",
            name="uq_normalized_signal_id_revision",
        ),
        UniqueConstraint(
            "raw_message_id",
            "parser_version",
            name="uq_normalized_signal_message_version",
        ),
        Index("ix_normalized_signal_signal_id", "signal_id"),
        Index("ix_normalized_signal_channel_topic", "channel_id", "topic_id"),
    )

    signal_row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_message_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("telegram_message_versions.source_event_id", ondelete="CASCADE"),
        nullable=False,
    )
    normalizer_version: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(30), nullable=True)
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    entry_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    entry_values: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    stop_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_origin: Mapped[str] = mapped_column(String(20), nullable=False)
    take_profits: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    evidence_spans: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    link_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SignalParseCheckpoint(Base):
    """Progress cursor for `parse_signals.py`, keyed by `(channel_id, topic_id, parser_version)`.

    Unlike `NormalizedContent` (where every input row always produces
    exactly one output row, so "no output row yet" doubles as "not yet
    processed"), a `normalized_content` row may legitimately produce *no*
    `NormalizedSignal` row at all (promotional/non-signal text). Without a
    separate cursor, a batch run would re-select and re-skip those same
    rows forever. The cursor is a `(received_at, raw_message_id)` pair so
    ties on an identical `received_at` are still ordered deterministically.
    A `parser_version` bump starts a fresh cursor (new PK), naturally
    reprocessing full history under the new version without disturbing the
    old version's checkpoint.
    """

    __tablename__ = "signal_parse_checkpoints"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_signal_parse_checkpoint_channel_positive"),
        CheckConstraint("topic_id >= 0", name="ck_signal_parse_checkpoint_topic_non_negative"),
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default="0")
    parser_version: Mapped[str] = mapped_column(String(20), primary_key=True)
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_raw_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SignalDecisionRequest(Base):
    """One immutable row per `normalized_signals` revision actually notified via Control Bot.

    Created only *after* the Telegram send succeeds, so there is never a
    placeholder row needing a later update -- `request_id` is derived from
    `signal_row_id`, so a given revision is requested at most once.
    `expires_at` inherits the underlying signal's own `expires_at`; Phase 4
    has no live execution urgency to justify a separate, shorter countdown.

    `nonce` is `UNIQUE`: Telegram caps inline-button callback data at 64
    bytes, too small to carry the full `request_id` alongside it, so a
    button tap is resolved back to its request by `nonce` alone
    (`signal_decisions.find_request_by_nonce`).
    """

    __tablename__ = "signal_decision_requests"
    __table_args__ = (
        UniqueConstraint("signal_row_id", name="uq_signal_decision_request_signal_row"),
        UniqueConstraint("nonce", name="uq_signal_decision_request_nonce"),
        Index("ix_signal_decision_request_signal_id", "signal_id"),
    )

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_row_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("normalized_signals.signal_row_id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalDecisionEvent(Base):
    """Append-only log of every decision attempt by the allowlisted actor against a request.

    `event_id` is derived from `(request_id, telegram_callback_query_id)` --
    Telegram's own callback-query ID is the natural idempotency key, since
    Telegram may redeliver a button tap at-least-once. Only the allowlisted
    actor's attempts reach this table; an unauthorized sender is rejected
    and logged before anything is written (same convention as
    `channel_policy.py`'s exclusion logging), so there is no
    `actor_user_id` CHECK here -- the allowlist is runtime config, not data.
    """

    __tablename__ = "signal_decision_events"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('APPROVED', 'REJECTED', 'REJECTED_STALE', 'REJECTED_EXPIRED')",
            name="ck_signal_decision_event_outcome_valid",
        ),
        Index("ix_signal_decision_event_request_id", "request_id"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("signal_decision_requests.request_id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_stop_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    approved_take_profits: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SignalDecisionEdit(Base):
    """Append-only log of every stop-loss/take-profit edit made before a decision.

    Not a partial diff: each row is a **full draft snapshot** (both fields,
    even if only one was actually changed this time), so "the current
    draft" is always just "the latest row by revision" for a given
    `request_id` -- no merge logic needed. `revision` is scoped per
    `request_id`, starting at 1 (mirrors `normalized_signals.revision`'s
    per-aggregate numbering). If no edit row exists yet for a request, the
    current draft is the original `NormalizedSignal`'s own
    `stop_value`/`take_profits` -- see `signal_decisions.load_current_draft`.
    """

    __tablename__ = "signal_decision_edits"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_signal_decision_edit_revision_positive"),
        UniqueConstraint("request_id", "revision", name="uq_signal_decision_edit_request_revision"),
    )

    edit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("signal_decision_requests.request_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_value: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    take_profits: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BinanceSymbolSnapshot(Base):
    """A versioned, append-only fetch of Binance USD(S)-M futures `exchangeInfo` (FR-002).

    Distinct from the spec's `market_snapshot` entity (per-symbol,
    time-bound price/mark evidence -- almost certainly a Phase 6 concern for
    BR-006 order-time freshness checks); this table records *which symbols
    exist and are active*, consumed by `normalization.resolve_symbol` for
    dynamic-scope (`BINANCE_USDM_ACTIVE_PERPETUAL`) resolution. `snapshot_id`
    bakes in `fetched_at`, so this is an append-only audit trail, not a
    dedup mechanism -- `load_latest_snapshot` always reads the newest row by
    `fetched_at`.
    """

    __tablename__ = "binance_symbol_snapshots"
    __table_args__ = (Index("ix_binance_symbol_snapshots_fetched_at", "fetched_at"),)

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_symbols: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Thesis(Base):
    """A schema-valid ANALYSIS proposition extracted from one `normalized_content` row (FR-012).

    `thesis_row_id` is this row's own identity, derived from
    `(content_id, extraction_schema_version, llm_model)` -- idempotent
    re-extraction, and a schema/prompt/model change appends fresh rows
    rather than overwriting old ones (same convention as `NormalizedContent`/
    `NormalizedSignal`). `thesis_id` is the separate, stable *aggregate*
    identity a later Strategy Contract/Market Confirmation slice will reuse
    across lifecycle revisions; this slice always writes `revision=0` and
    never produces anything beyond `DRAFT`/`INSUFFICIENT_DATA` -- `status`'s
    CHECK constraint is deliberately narrower than the full
    `DRAFT/MONITORING/CONFIRMED/INVALIDATED/EXPIRED/INSUFFICIENT_DATA`
    lifecycle for that reason (P3-MAJOR-004 was exactly about columns
    nothing consumes yet; this avoids declaring lifecycle states no code
    produces or checks). `media_included` is always `False` in this slice
    (BR-003): only `source_text_included` is ever sent to the LLM, never
    the chart image.
    """

    __tablename__ = "thesis"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'INSUFFICIENT_DATA')",
            name="ck_thesis_status_valid",
        ),
        CheckConstraint(
            "primary_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')",
            name="ck_thesis_primary_direction_valid",
        ),
        CheckConstraint(
            "confidence_status IN ('HIGH', 'MEDIUM', 'LOW', 'INSUFFICIENT_DATA')",
            name="ck_thesis_confidence_status_valid",
        ),
        CheckConstraint("revision >= 0", name="ck_thesis_revision_non_negative"),
        UniqueConstraint(
            "content_id",
            "extraction_schema_version",
            "llm_model",
            name="uq_thesis_content_extraction",
        ),
        UniqueConstraint(
            "thesis_id",
            "revision",
            "extraction_schema_version",
            "llm_model",
            name="uq_thesis_id_revision",
        ),
        Index("ix_thesis_channel_topic", "channel_id", "topic_id"),
        Index("ix_thesis_thesis_id", "thesis_id"),
    )

    thesis_row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thesis_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("normalized_content.content_id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    extraction_schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    llm_schema_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    primary_direction: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence_status: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_quotes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    source_text_included: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    media_included: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    raw_llm_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ThesisExtractionCheckpoint(Base):
    """Progress cursor for `extract_theses.py`, keyed by

    `(channel_id, topic_id, extraction_schema_version, llm_model)`.

    Same rationale as `SignalParseCheckpoint`: a `normalized_content` row
    may legitimately produce no `thesis` row at all (unauthorized, no
    resolvable symbol, or a provider/validation failure), so "row exists"
    idempotency alone would re-select and re-attempt those same rows
    forever. The cursor is a `(created_at, content_id)` pair so ties on an
    identical `created_at` are still ordered deterministically. A schema or
    model change starts a fresh cursor (new PK), naturally reprocessing full
    history under the new version without disturbing the old version's
    checkpoint or its rows.
    """

    __tablename__ = "thesis_extraction_checkpoints"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_thesis_extraction_checkpoint_channel_positive"),
        CheckConstraint("topic_id >= 0", name="ck_thesis_extraction_checkpoint_topic_non_negative"),
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, server_default="0")
    extraction_schema_version: Mapped[str] = mapped_column(String(20), primary_key=True)
    llm_model: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_content_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
