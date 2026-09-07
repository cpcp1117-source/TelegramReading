from __future__ import annotations

from datetime import date, datetime
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
