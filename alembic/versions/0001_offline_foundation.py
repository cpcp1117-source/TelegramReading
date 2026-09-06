"""Create the Phase 1 offline foundation schema.

Revision ID: 0001_offline_foundation
Revises: None
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_offline_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_audit_events_aggregate",
        "audit_events",
        ["aggregate_type", "aggregate_id", "recorded_at"],
    )

    op.create_table(
        "consumer_checkpoints",
        sa.Column("consumer_name", sa.String(length=100), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("last_sequence >= 0", name="ck_checkpoint_non_negative"),
    )

    op.create_table(
        "mock_message_receipts",
        sa.Column("source_event_id", sa.String(length=64), primary_key=True),
        sa.Column("consumer_name", sa.String(length=100), nullable=False),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["audit_event_id"], ["audit_events.event_id"]),
        sa.UniqueConstraint(
            "consumer_name", "sequence_no", name="uq_mock_receipt_consumer_sequence"
        ),
        sa.UniqueConstraint(
            "channel_id",
            "message_id",
            "edit_version",
            name="uq_mock_receipt_message_version",
        ),
        sa.CheckConstraint("message_id > 0", name="ck_mock_message_id_positive"),
        sa.CheckConstraint("edit_version >= 0", name="ck_mock_edit_version_non_negative"),
        sa.CheckConstraint("sequence_no > 0", name="ck_mock_sequence_positive"),
    )

    op.execute(
        """
        CREATE FUNCTION reject_audit_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_or_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_or_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_event_mutation()")
    op.drop_table("mock_message_receipts")
    op.drop_table("consumer_checkpoints")
    op.drop_index("ix_audit_events_aggregate", table_name="audit_events")
    op.drop_table("audit_events")
