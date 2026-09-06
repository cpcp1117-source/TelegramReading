"""Add Phase 2 Telegram read-only collector tables.

Revision ID: 0003_telegram_readonly_collector
Revises: 0002_transactional_outbox
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_telegram_readonly_collector"
down_revision: str | None = "0002_transactional_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_collector_checkpoints",
        sa.Column("channel_id", sa.BigInteger(), primary_key=True),
        sa.Column("last_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("channel_id > 0", name="ck_telegram_checkpoint_channel_positive"),
        sa.CheckConstraint(
            "last_message_id >= 0", name="ck_telegram_checkpoint_message_non_negative"
        ),
    )
    op.create_table(
        "telegram_message_versions",
        sa.Column("source_event_id", sa.String(length=64), primary_key=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("event_kind", sa.String(length=20), nullable=False),
        sa.Column("is_backfill", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edit_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("forward_origin_type", sa.String(length=20), nullable=True),
        sa.Column("forward_origin_id", sa.BigInteger(), nullable=True),
        sa.Column("forward_message_id", sa.BigInteger(), nullable=True),
        sa.Column("forward_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("media_sha256", sa.String(length=64), nullable=True),
        sa.Column("media_path", sa.String(length=500), nullable=True),
        sa.Column("media_mime_type", sa.String(length=150), nullable=True),
        sa.Column("media_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["audit_event_id"], ["audit_events.event_id"]),
        sa.UniqueConstraint(
            "channel_id", "message_id", "edit_version", name="uq_telegram_message_version"
        ),
        sa.CheckConstraint("channel_id > 0", name="ck_telegram_message_channel_positive"),
        sa.CheckConstraint("message_id > 0", name="ck_telegram_message_id_positive"),
        sa.CheckConstraint("edit_version >= 0", name="ck_telegram_edit_version_non_negative"),
        sa.CheckConstraint(
            "reply_to_message_id IS NULL OR reply_to_message_id > 0",
            name="ck_telegram_reply_message_positive",
        ),
        sa.CheckConstraint(
            "forward_origin_id IS NULL OR forward_origin_id > 0",
            name="ck_telegram_forward_origin_positive",
        ),
        sa.CheckConstraint(
            "forward_message_id IS NULL OR forward_message_id > 0",
            name="ck_telegram_forward_message_positive",
        ),
        sa.CheckConstraint(
            "media_size_bytes IS NULL OR media_size_bytes >= 0",
            name="ck_telegram_media_size_non_negative",
        ),
    )
    op.create_index(
        "ix_telegram_message_channel_message",
        "telegram_message_versions",
        ["channel_id", "message_id", "edit_version"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_telegram_message_version_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'telegram_message_versions is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER telegram_message_versions_no_update_or_delete
        BEFORE UPDATE OR DELETE ON telegram_message_versions
        FOR EACH ROW EXECUTE FUNCTION reject_telegram_message_version_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS telegram_message_versions_no_update_or_delete "
        "ON telegram_message_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_telegram_message_version_mutation()")
    op.drop_index("ix_telegram_message_channel_message", table_name="telegram_message_versions")
    op.drop_table("telegram_message_versions")
    op.drop_table("telegram_collector_checkpoints")
