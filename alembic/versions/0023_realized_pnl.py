"""One realized-PnL row per closed position, for the Web Console PnL page.

Until now a close's exit price, quantity and realized PnL lived only inside the
`execution_position_closed` event payload (`fill` is never populated, see
P6-LIMIT-012), which cannot be summed or filtered. `position_monitor` now writes
this row in the same transaction as that event; this migration backfills it from
the events already recorded. Append-only, like the other trade records.

`realized_pnl` is Binance's own per-trade `realizedPnl` summed over the closing
fills (gross, before fees); `exit_commission` is the closing fills' commission.
Both are NULL when the monitor could not load the fills -- the close itself is
still recorded so the trade count stays right.

Revision ID: 0023_realized_pnl
Revises: 0022_raw_retention_two_months
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_realized_pnl"
down_revision: str | None = "0022_raw_retention_two_months"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "realized_pnl",
        sa.Column(
            "intent_id",
            sa.String(length=64),
            sa.ForeignKey("trade_intent.intent_id"),
            primary_key=True,
        ),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("close_reason", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("exit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("exit_commission", sa.Numeric(20, 8), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("side IN ('LONG', 'SHORT')", name="ck_realized_pnl_side_valid"),
        sa.CheckConstraint(
            "close_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'OTHER')",
            name="ck_realized_pnl_close_reason_valid",
        ),
    )
    op.create_index("ix_realized_pnl_closed_at", "realized_pnl", ["closed_at"])
    op.execute(
        """
        CREATE FUNCTION reject_realized_pnl_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'realized_pnl is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER realized_pnl_no_update
        BEFORE UPDATE ON realized_pnl
        FOR EACH ROW EXECUTE FUNCTION reject_realized_pnl_update()
        """
    )
    # Backfill from the close events already written. The payload stores the
    # Chinese close-reason label and the PnL figures as strings.
    op.execute(
        """
        INSERT INTO realized_pnl (
            intent_id, channel_id, symbol, side, close_reason, quantity, entry_price,
            exit_price, realized_pnl, exit_commission, opened_at, closed_at
        )
        SELECT DISTINCT ON (ti.intent_id)
            ti.intent_id,
            ti.channel_id,
            ti.symbol,
            ti.side,
            CASE ae.payload->>'close_reason'
                WHEN '停損觸發' THEN 'STOP_LOSS'
                WHEN '停利觸發' THEN 'TAKE_PROFIT'
                ELSE 'OTHER'
            END,
            NULLIF(ae.payload->>'closed_quantity', '')::numeric,
            entry.avg_fill_price,
            NULLIF(ae.payload->>'exit_price', '')::numeric,
            NULLIF(ae.payload->>'realized_pnl', '')::numeric,
            NULLIF(ae.payload->>'exit_commission', '')::numeric,
            (SELECT min(ol.transitioned_at) FROM order_lifecycle ol
              WHERE ol.intent_id = ti.intent_id AND ol.state = 'FILLED'),
            ae.recorded_at
        FROM audit_events ae
        JOIN trade_intent ti ON ti.intent_id = ae.aggregate_id
        LEFT JOIN exchange_order entry
               ON entry.intent_id = ti.intent_id AND entry.purpose = 'ENTRY'
        WHERE ae.event_type = 'execution_position_closed'
          AND ae.aggregate_type = 'trade_intent'
        ORDER BY ti.intent_id, ae.recorded_at
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS realized_pnl_no_update ON realized_pnl")
    op.execute("DROP FUNCTION IF EXISTS reject_realized_pnl_update()")
    op.drop_index("ix_realized_pnl_closed_at", table_name="realized_pnl")
    op.drop_table("realized_pnl")
