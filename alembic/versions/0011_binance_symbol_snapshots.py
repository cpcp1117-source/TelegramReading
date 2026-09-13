"""Add binance_symbol_snapshots (Phase 5 Slice 1 -- dynamic symbol resolution).

Records versioned fetches of Binance USD(S)-M futures `exchangeInfo`, so
`normalization.resolve_symbol` can validate `BINANCE_USDM_ACTIVE_PERPETUAL`
candidates against real, active perpetual symbols instead of always
returning `PENDING_MARKET_DATA` (FR-002, Data Invariant #2). This is
distinct from the spec's `market_snapshot` entity (per-symbol price/mark
evidence, a Phase 6 concern) -- this table tracks which symbols exist and
are active, not their price. `snapshot_id` bakes in `fetched_at`, so this
is an append-only audit trail, not a dedup mechanism; the read path always
takes the newest row by `fetched_at`. No FK/cascade: this is a standalone
reference table, not tied to any raw message.

Revision ID: 0011_binance_symbol_snapshots
Revises: 0010_decision_nonce_unique
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_binance_symbol_snapshots"
down_revision: str | None = "0010_decision_nonce_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "binance_symbol_snapshots",
        sa.Column("snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "active_symbols",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("symbol_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_binance_symbol_snapshots_fetched_at",
        "binance_symbol_snapshots",
        ["fetched_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_binance_symbol_snapshots_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'binance_symbol_snapshots is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER binance_symbol_snapshots_no_update
        BEFORE UPDATE ON binance_symbol_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_binance_symbol_snapshots_update()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS binance_symbol_snapshots_no_update ON binance_symbol_snapshots"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_binance_symbol_snapshots_update()")
    op.drop_index("ix_binance_symbol_snapshots_fetched_at", table_name="binance_symbol_snapshots")
    op.drop_table("binance_symbol_snapshots")
