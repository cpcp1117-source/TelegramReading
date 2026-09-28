"""Add Execution Gateway tables (Phase 6 Slice 2a): order_lifecycle, exchange_order,

protection_order, fill, position_snapshot.

`order_lifecycle` is append-only (one row per transition, matching this
project's "append revisions" convention). `exchange_order` is the one
exception to that convention in this migration -- it mirrors a single
mutable Binance order resource and is updated in place; `fill` preserves
the immutable trade history separately. `position_snapshot` is an
append-only time series. `system_control_state` is deliberately not added
-- out of scope for this slice (FR-021/System Pause wiring is later).

Revision ID: 0017_execution_gateway_tables
Revises: 0016_risk_config_flat_sizing
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_execution_gateway_tables"
down_revision: str | None = "0016_risk_config_flat_sizing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_lifecycle",
        sa.Column("lifecycle_row_id", sa.String(64), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(64),
            sa.ForeignKey("trade_intent.intent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "decision_id",
            sa.String(64),
            sa.ForeignKey("risk_decision.decision_id"),
            nullable=False,
        ),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("entry_client_order_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("transition_reason", sa.String(200), nullable=True),
        sa.Column("protection_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "transitioned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("intent_id", "revision", name="uq_order_lifecycle_intent_revision"),
        sa.CheckConstraint(
            "environment = 'TESTNET'", name="ck_order_lifecycle_environment_testnet"
        ),
        sa.CheckConstraint("side IN ('LONG', 'SHORT')", name="ck_order_lifecycle_side_valid"),
        sa.CheckConstraint(
            "state IN ('PENDING_SUBMIT', 'SUBMITTED', 'PARTIALLY_FILLED', 'FILLED', "
            "'PROTECTION_PENDING', 'PROTECTED', 'EMERGENCY_CLOSING', 'CLOSING', 'CLOSED', "
            "'FAILED_RECONCILIATION')",
            name="ck_order_lifecycle_state_valid",
        ),
    )
    op.create_index("ix_order_lifecycle_intent_id", "order_lifecycle", ["intent_id"])

    op.create_table(
        "exchange_order",
        sa.Column("order_row_id", sa.String(64), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(64),
            sa.ForeignKey("trade_intent.intent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("client_order_id", sa.String(36), nullable=False),
        sa.Column("exchange_order_id", sa.BigInteger(), nullable=True),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("position_side", sa.String(10), nullable=False, server_default="BOTH"),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("close_position", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("working_type", sa.String(20), nullable=True),
        sa.Column("requested_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("requested_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("requested_stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("client_order_id", name="uq_exchange_order_client_order_id"),
        sa.CheckConstraint(
            "purpose IN ('ENTRY', 'PROTECTION', 'EMERGENCY_CLOSE')",
            name="ck_exchange_order_purpose_valid",
        ),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_exchange_order_side_valid"),
        sa.CheckConstraint(
            "order_type IN ('MARKET', 'LIMIT', 'STOP_MARKET', 'TAKE_PROFIT_MARKET')",
            name="ck_exchange_order_type_valid",
        ),
        sa.CheckConstraint("position_side = 'BOTH'", name="ck_exchange_order_position_side_valid"),
        sa.CheckConstraint(
            "working_type IS NULL OR working_type IN ('CONTRACT_PRICE', 'MARK_PRICE')",
            name="ck_exchange_order_working_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'PENDING_CANCEL', "
            "'REJECTED', 'EXPIRED', 'EXPIRED_IN_MATCH', 'TRIGGERED', 'UNKNOWN')",
            name="ck_exchange_order_status_valid",
        ),
    )
    op.create_index("ix_exchange_order_intent_id", "exchange_order", ["intent_id"])

    op.create_table(
        "protection_order",
        sa.Column("protection_row_id", sa.String(64), primary_key=True),
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
        sa.Column("protected_quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("confirmation_deadline", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("intent_id", name="uq_protection_order_intent"),
        sa.CheckConstraint(
            "state IN ('PENDING', 'CONFIRMED', 'FAILED', 'EMERGENCY_CLOSED')",
            name="ck_protection_order_state_valid",
        ),
        sa.CheckConstraint("protected_quantity > 0", name="ck_protection_order_quantity_positive"),
    )

    op.create_table(
        "fill",
        sa.Column("fill_row_id", sa.String(64), primary_key=True),
        sa.Column(
            "order_row_id",
            sa.String(64),
            sa.ForeignKey("exchange_order.order_row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("exchange_trade_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("commission", sa.Numeric(20, 8), nullable=False, server_default="0"),
        sa.Column("commission_asset", sa.String(10), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("order_row_id", "exchange_trade_id", name="uq_fill_order_trade"),
        sa.CheckConstraint("quantity > 0", name="ck_fill_quantity_positive"),
        sa.CheckConstraint("price > 0", name="ck_fill_price_positive"),
    )

    op.create_table(
        "position_snapshot",
        sa.Column("snapshot_row_id", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("position_side", sa.String(10), nullable=False, server_default="BOTH"),
        sa.Column("position_amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("mark_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("unrealized_pnl", sa.Numeric(20, 8), nullable=True),
        sa.Column("initial_margin", sa.Numeric(20, 8), nullable=True),
        sa.Column("maint_margin", sa.Numeric(20, 8), nullable=True),
        sa.Column("leverage", sa.Integer(), nullable=True),
        sa.Column("margin_type", sa.String(10), nullable=True),
        sa.Column("liquidation_price", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "triggered_by_intent_id",
            sa.String(64),
            sa.ForeignKey("trade_intent.intent_id"),
            nullable=True,
        ),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "environment = 'TESTNET'", name="ck_position_snapshot_environment_testnet"
        ),
        sa.CheckConstraint(
            "position_side = 'BOTH'", name="ck_position_snapshot_position_side_valid"
        ),
        sa.CheckConstraint(
            "margin_type IS NULL OR margin_type IN ('ISOLATED', 'CROSS')",
            name="ck_position_snapshot_margin_type_valid",
        ),
    )
    op.create_index(
        "ix_position_snapshot_symbol_fetched_at", "position_snapshot", ["symbol", "fetched_at"]
    )


def downgrade() -> None:
    op.drop_table("position_snapshot")
    op.drop_table("fill")
    op.drop_table("protection_order")
    op.drop_table("exchange_order")
    op.drop_table("order_lifecycle")
