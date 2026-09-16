"""Drop risk_config_snapshot.max_source_age_seconds (Phase 6 Slice 1 -- BR-006 revised).

A raw elapsed-time freshness check against a signal's own post time was
found (while planning this fix) to reject nearly every real signal under
this project's still-manual Control Bot approval workflow (BR-002): a
human reasonably takes longer than 60 seconds to see a notification and
tap Approve, and that delay by itself says nothing about whether the
trade is still safe. Replaced with BR-006's own price-deviation leg for
`MARKET` entries (compare price near the signal's post time against the
current price) -- see `risk_engine.py`'s module docstring, "BR-006
freshness, revised", and docs/phase-6/known-issues.md P6-LIMIT-004.

`max_price_deviation_bps` (added in migration 0014, unused until now) is
what actually gates this from here on; `max_source_age_seconds` is no
longer read by any code path. Dropped rather than left as dead, misleading
config -- the exact "shipping columns nobody consumes" pattern Phase 3's
P3-MAJOR-004 already criticized once. Only one real `risk_config_snapshot`
row exists as of this writing, itself from an explicit test run (not
production data) -- safe to drop without a backfill.

Revision ID: 0015_drop_risk_config_source_age
Revises: 0014_trade_intent_risk_decision
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_drop_risk_config_source_age"
down_revision: str | None = "0014_trade_intent_risk_decision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("risk_config_snapshot", "max_source_age_seconds")


def downgrade() -> None:
    op.add_column(
        "risk_config_snapshot",
        sa.Column("max_source_age_seconds", sa.Integer(), nullable=False, server_default="60"),
    )
