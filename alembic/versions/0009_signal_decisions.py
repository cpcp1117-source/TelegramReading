"""Add signal_decision_requests and signal_decision_events (Phase 4 Slice 3 -- Control Bot).

`signal_decision_requests` is one immutable row per `normalized_signals`
revision actually notified via Control Bot -- created only after the
Telegram send succeeds, so there is never a placeholder row needing a
later update. `signal_decision_events` is an append-only log of every
decision attempt by the allowlisted actor against a request (event_id
derived from Telegram's own callback-query ID for idempotency against
at-least-once delivery). Both tables reject UPDATE via the same trigger
pattern as normalized_content/normalized_signals; DELETE cascades from
normalized_signals -> signal_decision_requests -> signal_decision_events,
so retention cleanup purging an expired raw message cleans up its
decision history automatically too.

Revision ID: 0009_signal_decisions
Revises: 0008_normalized_signals
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_signal_decisions"
down_revision: str | None = "0008_normalized_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_decision_requests",
        sa.Column("request_id", sa.String(length=64), primary_key=True),
        sa.Column("signal_id", sa.String(length=64), nullable=False),
        sa.Column("signal_row_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["signal_row_id"],
            ["normalized_signals.signal_row_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("signal_row_id", name="uq_signal_decision_request_signal_row"),
    )
    op.create_index(
        "ix_signal_decision_request_signal_id", "signal_decision_requests", ["signal_id"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_signal_decision_request_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'signal_decision_requests is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER signal_decision_requests_no_update
        BEFORE UPDATE ON signal_decision_requests
        FOR EACH ROW EXECUTE FUNCTION reject_signal_decision_request_update()
        """
    )

    op.create_table(
        "signal_decision_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["signal_decision_requests.request_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "outcome IN ('APPROVED', 'REJECTED', 'REJECTED_STALE', 'REJECTED_EXPIRED')",
            name="ck_signal_decision_event_outcome_valid",
        ),
    )
    op.create_index("ix_signal_decision_event_request_id", "signal_decision_events", ["request_id"])
    op.execute(
        """
        CREATE FUNCTION reject_signal_decision_event_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'signal_decision_events is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER signal_decision_events_no_update
        BEFORE UPDATE ON signal_decision_events
        FOR EACH ROW EXECUTE FUNCTION reject_signal_decision_event_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS signal_decision_events_no_update ON signal_decision_events")
    op.execute("DROP FUNCTION IF EXISTS reject_signal_decision_event_update()")
    op.drop_index("ix_signal_decision_event_request_id", table_name="signal_decision_events")
    op.drop_table("signal_decision_events")

    op.execute(
        "DROP TRIGGER IF EXISTS signal_decision_requests_no_update ON signal_decision_requests"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_signal_decision_request_update()")
    op.drop_index("ix_signal_decision_request_signal_id", table_name="signal_decision_requests")
    op.drop_table("signal_decision_requests")
