"""The user's answer to a daily-loss kill-switch alert: stop new orders for the day, or not.

User decision 2026-10-04: reaching the daily loss limit (counted from UTC 00:00 =
Taiwan 08:00) does not block trading by itself. The bot asks in Telegram; a STOP
answer pauses new orders until the next UTC midnight (`paused_until`), CONTINUE
records that the user chose to keep trading. One answer per alert
(`source_event_id` is the alert's outbox event). Append-only.

Revision ID: 0024_trading_pause
Revises: 0023_realized_pnl
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_trading_pause"
down_revision: str | None = "0023_realized_pnl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_pause_decisions",
        sa.Column("decision_id", sa.String(length=64), primary_key=True),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("source_event_id", name="uq_trading_pause_source_event"),
        sa.CheckConstraint(
            "decision IN ('STOP', 'CONTINUE')", name="ck_trading_pause_decision_valid"
        ),
        sa.CheckConstraint(
            "(decision = 'STOP') = (paused_until IS NOT NULL)",
            name="ck_trading_pause_until_only_for_stop",
        ),
    )
    op.create_index(
        "ix_trading_pause_decisions_paused_until", "trading_pause_decisions", ["paused_until"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_trading_pause_decision_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'trading_pause_decisions is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trading_pause_decisions_no_update
        BEFORE UPDATE ON trading_pause_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_trading_pause_decision_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trading_pause_decisions_no_update ON trading_pause_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_trading_pause_decision_update()")
    op.drop_index("ix_trading_pause_decisions_paused_until", table_name="trading_pause_decisions")
    op.drop_table("trading_pause_decisions")
