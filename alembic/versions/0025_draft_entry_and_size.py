"""Let the user edit a draft's entry price and position size before approving it.

`signal_decision_edits` (full draft snapshots) and `signal_decision_events` (what was approved) gain
three nullable columns: an overridden `entry_type`/`entry_value` (NULL = use the signal's own entry)
and `position_size_pct` (NULL = use the configured default). `normalized_signals` gains
`suggested_size_pct`, a producer's suggested size (e.g. a smaller default for analysis-channel
drafts), which becomes the draft's starting size.

Revision ID: 0025_draft_entry_and_size
Revises: 0024_trading_pause
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_draft_entry_and_size"
down_revision: str | None = "0024_trading_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("normalized_signals", sa.Column("suggested_size_pct", sa.Numeric(6, 4)))
    op.create_check_constraint(
        "ck_normalized_signal_suggested_size_valid",
        "normalized_signals",
        "suggested_size_pct IS NULL OR (suggested_size_pct > 0 AND suggested_size_pct <= 1)",
    )

    op.add_column("signal_decision_edits", sa.Column("entry_type", sa.String(length=10)))
    op.add_column("signal_decision_edits", sa.Column("entry_value", sa.Numeric(20, 8)))
    op.add_column("signal_decision_edits", sa.Column("position_size_pct", sa.Numeric(6, 4)))
    op.create_check_constraint(
        "ck_signal_decision_edit_entry_type_valid",
        "signal_decision_edits",
        "entry_type IS NULL OR entry_type IN ('MARKET', 'LIMIT')",
    )
    op.create_check_constraint(
        "ck_signal_decision_edit_size_valid",
        "signal_decision_edits",
        "position_size_pct IS NULL OR (position_size_pct > 0 AND position_size_pct <= 1)",
    )

    op.add_column("signal_decision_events", sa.Column("approved_entry_type", sa.String(length=10)))
    op.add_column("signal_decision_events", sa.Column("approved_entry_value", sa.Numeric(20, 8)))
    op.add_column(
        "signal_decision_events", sa.Column("approved_position_size_pct", sa.Numeric(6, 4))
    )
    op.create_check_constraint(
        "ck_signal_decision_event_entry_type_valid",
        "signal_decision_events",
        "approved_entry_type IS NULL OR approved_entry_type IN ('MARKET', 'LIMIT')",
    )
    op.create_check_constraint(
        "ck_signal_decision_event_size_valid",
        "signal_decision_events",
        "approved_position_size_pct IS NULL OR "
        "(approved_position_size_pct > 0 AND approved_position_size_pct <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_signal_decision_event_size_valid", "signal_decision_events", type_="check"
    )
    op.drop_constraint(
        "ck_signal_decision_event_entry_type_valid", "signal_decision_events", type_="check"
    )
    op.drop_column("signal_decision_events", "approved_position_size_pct")
    op.drop_column("signal_decision_events", "approved_entry_value")
    op.drop_column("signal_decision_events", "approved_entry_type")
    op.drop_constraint("ck_signal_decision_edit_size_valid", "signal_decision_edits", type_="check")
    op.drop_constraint(
        "ck_signal_decision_edit_entry_type_valid", "signal_decision_edits", type_="check"
    )
    op.drop_column("signal_decision_edits", "position_size_pct")
    op.drop_column("signal_decision_edits", "entry_value")
    op.drop_column("signal_decision_edits", "entry_type")
    op.drop_constraint(
        "ck_normalized_signal_suggested_size_valid", "normalized_signals", type_="check"
    )
    op.drop_column("normalized_signals", "suggested_size_pct")
