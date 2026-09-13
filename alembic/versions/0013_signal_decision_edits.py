"""Add signal_decision_edits and approved-value columns on signal_decision_events.

Control Bot enhancement (not a new Phase -- extends Phase 4's already-
accepted Signal lifecycle/Control Bot capability): lets the user edit a
pending signal's stop-loss/take-profit before approving, still never
touching Binance. `signal_decision_edits` is append-only, one row per edit,
storing a full draft snapshot (both fields, even if only one changed) so
"current draft" is always just the latest row by revision -- no merge
logic needed. `signal_decision_events` gains two nullable columns
(`approved_stop_value`/`approved_take_profits`) so a decision's own row is
a self-contained record of exactly what was approved/rejected, without a
reader needing to join back through edit history.

Revision ID: 0013_signal_decision_edits
Revises: 0012_thesis
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_signal_decision_edits"
down_revision: str | None = "0012_thesis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal_decision_events",
        sa.Column("approved_stop_value", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "signal_decision_events",
        sa.Column("approved_take_profits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "signal_decision_edits",
        sa.Column("edit_id", sa.String(length=64), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("stop_value", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "take_profits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "edited_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["signal_decision_requests.request_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "request_id", "revision", name="uq_signal_decision_edit_request_revision"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_signal_decision_edit_revision_positive"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_signal_decision_edit_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'signal_decision_edits is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER signal_decision_edits_no_update
        BEFORE UPDATE ON signal_decision_edits
        FOR EACH ROW EXECUTE FUNCTION reject_signal_decision_edit_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS signal_decision_edits_no_update ON signal_decision_edits")
    op.execute("DROP FUNCTION IF EXISTS reject_signal_decision_edit_update()")
    op.drop_table("signal_decision_edits")
    op.drop_column("signal_decision_events", "approved_take_profits")
    op.drop_column("signal_decision_events", "approved_stop_value")
