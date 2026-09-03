"""Add the transactional outbox and mock reply metadata.

Revision ID: 0002_transactional_outbox
Revises: 0001_offline_foundation
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_transactional_outbox"
down_revision: str | None = "0001_offline_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mock_message_receipts",
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_mock_reply_message_id_positive",
        "mock_message_receipts",
        "reply_to_message_id IS NULL OR reply_to_message_id > 0",
    )
    op.create_table(
        "outbox_events",
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
    op.create_index("ix_outbox_events_recorded_at", "outbox_events", ["recorded_at"])
    op.create_table(
        "outbox_delivery_receipts",
        sa.Column("consumer_name", sa.String(length=100), primary_key=True),
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["outbox_events.event_id"]),
    )
    op.execute(
        """
        CREATE FUNCTION reject_outbox_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'outbox_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER outbox_events_no_update_or_delete
        BEFORE UPDATE OR DELETE ON outbox_events
        FOR EACH ROW EXECUTE FUNCTION reject_outbox_event_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS outbox_events_no_update_or_delete ON outbox_events")
    op.execute("DROP FUNCTION IF EXISTS reject_outbox_event_mutation()")
    op.drop_table("outbox_delivery_receipts")
    op.drop_index("ix_outbox_events_recorded_at", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_constraint("ck_mock_reply_message_id_positive", "mock_message_receipts", type_="check")
    op.drop_column("mock_message_receipts", "reply_to_message_id")
