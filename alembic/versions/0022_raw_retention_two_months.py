"""Keep raw messages (and the signals/approvals derived from them) for 60 days.

User decision 2026-10-04: signals and their approval records are kept for two
months, then deleted. They are deleted through the existing raw-message
retention path -- deleting a `telegram_message_versions` row CASCADEs into
normalized content, signals, decision requests/events/edits and theses -- so
every channel's `raw_retention_days` (and the column default for new channels)
moves from 7 to 60.

An approval that actually produced an order is kept: the same CASCADE would
otherwise reach `trade_intent` and from there every order, fill and lifecycle
row. `retention_cleanup` already skips such messages; the trigger below is the
database-side backstop, refusing to delete any `trade_intent` that has an
`order_lifecycle` row no matter which delete path reaches it.

Revision ID: 0022_raw_retention_two_months
Revises: 0021_analysis_setups
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_raw_retention_two_months"
down_revision: str | None = "0021_analysis_setups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETENTION_DAYS = 60
PREVIOUS_RETENTION_DAYS = 7


def upgrade() -> None:
    op.execute(f"UPDATE channel_policies SET raw_retention_days = {RETENTION_DAYS}")
    op.alter_column(
        "channel_policies",
        "raw_retention_days",
        existing_type=sa.Integer(),
        server_default=str(RETENTION_DAYS),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_trade_intent_delete_with_orders()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM order_lifecycle WHERE intent_id = OLD.intent_id) THEN
                RAISE EXCEPTION 'trade_intent % has order records and cannot be deleted',
                    OLD.intent_id;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trade_intent_no_delete_with_orders
        BEFORE DELETE ON trade_intent
        FOR EACH ROW EXECUTE FUNCTION reject_trade_intent_delete_with_orders()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trade_intent_no_delete_with_orders ON trade_intent")
    op.execute("DROP FUNCTION IF EXISTS reject_trade_intent_delete_with_orders()")
    op.alter_column(
        "channel_policies",
        "raw_retention_days",
        existing_type=sa.Integer(),
        server_default=str(PREVIOUS_RETENTION_DAYS),
    )
    op.execute(
        f"UPDATE channel_policies SET raw_retention_days = {PREVIOUS_RETENTION_DAYS} "
        f"WHERE raw_retention_days = {RETENTION_DAYS}"
    )
