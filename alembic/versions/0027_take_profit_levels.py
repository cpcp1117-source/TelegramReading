"""Allow several take-profit orders per trade intent (one per level).

User decision 2026-10-05: a signal's filled quantity is split across its take-profit targets.
`take_profit_order` was one row per intent (`uq_take_profit_order_intent`); it becomes one row per
`(intent_id, level_index)`. Existing rows are level 0.

Revision ID: 0027_take_profit_levels
Revises: 0026_runtime_settings
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_take_profit_levels"
down_revision: str | None = "0026_runtime_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "take_profit_order",
        sa.Column("level_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_constraint("uq_take_profit_order_intent", "take_profit_order", type_="unique")
    op.create_unique_constraint(
        "uq_take_profit_order_intent_level", "take_profit_order", ["intent_id", "level_index"]
    )
    op.create_check_constraint(
        "ck_take_profit_order_level_non_negative", "take_profit_order", "level_index >= 0"
    )


def downgrade() -> None:
    # Only level 0 fits the old one-row-per-intent shape; higher levels are dropped.
    op.execute("DELETE FROM take_profit_order WHERE level_index > 0")
    op.drop_constraint(
        "ck_take_profit_order_level_non_negative", "take_profit_order", type_="check"
    )
    op.drop_constraint("uq_take_profit_order_intent_level", "take_profit_order", type_="unique")
    op.create_unique_constraint("uq_take_profit_order_intent", "take_profit_order", ["intent_id"])
    op.drop_column("take_profit_order", "level_index")
