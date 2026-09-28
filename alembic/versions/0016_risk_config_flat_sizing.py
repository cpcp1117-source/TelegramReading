"""Replace risk_config_snapshot's 3-cap sizing with a flat position_size_pct (Phase 6).

P6-LIMIT-005 (min of risk-based/single-margin-cap/total-margin-headroom)
superseded 2026-09-25 per the user: stop-loss is set by chart structure
(prior swing low/high), not derived from a risk-based stop distance, so a
sizing cap keyed to stop distance no longer matches how the user actually
trades. Sizing is now a flat `equity * position_size_pct` margin at
`leverage`, full stop -- see risk_engine.py's `RiskConfig.position_size_pct`
docstring and docs/phase-6/known-issues.md P6-NOTICE-007.

`max_single_trade_initial_margin_pct` is renamed to `position_size_pct`
(same column shape, new meaning: a fixed sizing target, not a cap) rather
than added as a new column -- `max_single_trade_risk_pct` and
`max_total_initial_margin_pct` are dropped outright, the same
"don't ship dead config" call as migration 0015. Only one real
`risk_config_snapshot` row exists as of this writing (a test run, not
production data) -- safe to alter without a backfill.

Revision ID: 0016_risk_config_flat_sizing
Revises: 0015_drop_risk_config_source_age
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_risk_config_flat_sizing"
down_revision: str | None = "0015_drop_risk_config_source_age"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "risk_config_snapshot",
        "max_single_trade_initial_margin_pct",
        new_column_name="position_size_pct",
    )
    op.drop_column("risk_config_snapshot", "max_single_trade_risk_pct")
    op.drop_column("risk_config_snapshot", "max_total_initial_margin_pct")


def downgrade() -> None:
    op.alter_column(
        "risk_config_snapshot",
        "position_size_pct",
        new_column_name="max_single_trade_initial_margin_pct",
    )
    op.add_column(
        "risk_config_snapshot",
        sa.Column(
            "max_single_trade_risk_pct", sa.Numeric(5, 4), nullable=False, server_default="0.03"
        ),
    )
    op.add_column(
        "risk_config_snapshot",
        sa.Column(
            "max_total_initial_margin_pct",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0.30",
        ),
    )
