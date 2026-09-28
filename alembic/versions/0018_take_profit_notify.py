"""Add take_profit_order table; add PROTECTION_FAILED to order_lifecycle.state (Phase 6 Slice 2a).

Per explicit user request 2026-09-29:
1. Execution Gateway now also places a take-profit order (first value of
   `trade_intent.take_profits`, if any) alongside the existing stop-loss
   protection order. `exchange_order.purpose` needs a new `'TAKE_PROFIT'`
   value for this (found live: the first real take-profit placement
   crashed on this exact CHECK constraint -- fixed here, in the same
   migration, since it had not yet been used with real committed rows).
2. A stop-loss protection order that cannot be confirmed no longer triggers
   an automatic Emergency Close -- the position is left open, unprotected,
   and the user is notified to handle it themselves. `order_lifecycle.state`
   needs a new terminal value, `PROTECTION_FAILED`, for this case.

Notifications themselves reuse the existing `outbox_events`/
`outbox_delivery_receipts` tables (Phase 1) -- no schema change needed for
that half of the request; only a new `load_pending_events` query function
and a Control Bot consumer loop, both pure application code.

Revision ID: 0018_take_profit_notify
Revises: 0017_execution_gateway_tables
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_take_profit_notify"
down_revision: str | None = "0017_execution_gateway_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATES = (
    "'PENDING_SUBMIT', 'SUBMITTED', 'PARTIALLY_FILLED', 'FILLED', "
    "'PROTECTION_PENDING', 'PROTECTED', 'EMERGENCY_CLOSING', 'CLOSING', 'CLOSED', "
    "'FAILED_RECONCILIATION'"
)
_NEW_STATES = (
    "'PENDING_SUBMIT', 'SUBMITTED', 'PARTIALLY_FILLED', 'FILLED', "
    "'PROTECTION_PENDING', 'PROTECTED', 'PROTECTION_FAILED', 'EMERGENCY_CLOSING', "
    "'CLOSING', 'CLOSED', 'FAILED_RECONCILIATION'"
)


def upgrade() -> None:
    op.drop_constraint("ck_order_lifecycle_state_valid", "order_lifecycle", type_="check")
    op.create_check_constraint(
        "ck_order_lifecycle_state_valid", "order_lifecycle", f"state IN ({_NEW_STATES})"
    )

    op.drop_constraint("ck_exchange_order_purpose_valid", "exchange_order", type_="check")
    op.create_check_constraint(
        "ck_exchange_order_purpose_valid",
        "exchange_order",
        "purpose IN ('ENTRY', 'PROTECTION', 'TAKE_PROFIT', 'EMERGENCY_CLOSE')",
    )

    op.create_table(
        "take_profit_order",
        sa.Column("take_profit_row_id", sa.String(64), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(64),
            sa.ForeignKey("trade_intent.intent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exchange_order_id",
            sa.String(64),
            sa.ForeignKey("exchange_order.order_row_id"),
            nullable=True,
        ),
        sa.Column("target_quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("target_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("intent_id", name="uq_take_profit_order_intent"),
        sa.CheckConstraint(
            "state IN ('PENDING', 'CONFIRMED', 'FAILED')",
            name="ck_take_profit_order_state_valid",
        ),
        sa.CheckConstraint("target_quantity > 0", name="ck_take_profit_order_quantity_positive"),
    )


def downgrade() -> None:
    op.drop_table("take_profit_order")
    op.drop_constraint("ck_exchange_order_purpose_valid", "exchange_order", type_="check")
    op.create_check_constraint(
        "ck_exchange_order_purpose_valid",
        "exchange_order",
        "purpose IN ('ENTRY', 'PROTECTION', 'EMERGENCY_CLOSE')",
    )
    op.drop_constraint("ck_order_lifecycle_state_valid", "order_lifecycle", type_="check")
    op.create_check_constraint(
        "ck_order_lifecycle_state_valid", "order_lifecycle", f"state IN ({_OLD_STATES})"
    )
