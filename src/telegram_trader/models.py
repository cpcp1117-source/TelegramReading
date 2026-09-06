from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    __tablename__ = "telegram_collector_checkpoints"
    __table_args__ = (
        CheckConstraint("channel_id > 0", name="ck_telegram_checkpoint_channel_positive"),
        CheckConstraint("last_message_id >= 0", name="ck_telegram_checkpoint_message_non_negative"),
    )

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
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
