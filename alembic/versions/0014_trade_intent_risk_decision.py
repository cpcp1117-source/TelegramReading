"""Add risk_config_snapshot, trade_intent, risk_decision (Phase 6 Slice 1 -- Risk Engine).

Turns an `APPROVED` `signal_decision_events` row into a `trade_intent`, then
evaluates it against fixed risk rules (BR-006/007/008/009/010/011/012) to
produce a `risk_decision` (FR-016). No Binance credential is touched by any
of this -- `risk-engine` per architecture.md has no credential at all; only
the not-yet-built Execution Gateway (Slice 2) will.

`trade_intent.intent_id` is derived from `origin_event_id` alone, so
re-processing the same approved decision is naturally idempotent
(NFR-002) -- the same "hash of the thing that caused this row to exist"
convention as every other derived table in this project.

`risk_config_snapshot` versions the risk-rule *parameters themselves*
(BR-008/009/010/011's numeric limits, plus this slice's own
`equity_baseline_usdt`), append-only, so a `risk_decision` can always be
replayed against the exact config that was live when it was made (Data
Invariant #5's "config snapshot" leg). Honest limitation, documented here
and in known-issues.md: `equity_baseline_usdt`/position-count/daily-loss
are Slice-1 *configured* values, not yet sourced from a real Binance
account -- that requires the Execution Gateway's own account query, Slice
2's job. BR-010's "existing manual positions also count" cannot actually
be satisfied until then either.

`risk_decision.reason_codes` must be non-empty when `verdict='REJECTED'`,
enforced by a CHECK using `jsonb_array_length` -- mirrors this project's
existing CHECK-constraint-over-application-trust convention.

Revision ID: 0014_trade_intent_risk_decision
Revises: 0013_signal_decision_edits
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_trade_intent_risk_decision"
down_revision: str | None = "0013_signal_decision_edits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_config_snapshot",
        sa.Column("config_snapshot_id", sa.String(length=64), primary_key=True),
        sa.Column("leverage", sa.Integer(), nullable=False),
        sa.Column("default_stop_roe_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("max_single_trade_risk_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("max_single_trade_initial_margin_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("max_total_initial_margin_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("max_concurrent_positions", sa.Integer(), nullable=False),
        sa.Column("daily_loss_kill_switch_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("max_source_age_seconds", sa.Integer(), nullable=False),
        sa.Column("max_receive_lag_seconds", sa.Integer(), nullable=False),
        sa.Column("max_price_deviation_bps", sa.Integer(), nullable=False),
        sa.Column("equity_baseline_usdt", sa.Numeric(20, 8), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("leverage > 0", name="ck_risk_config_leverage_positive"),
        sa.CheckConstraint(
            "max_concurrent_positions > 0", name="ck_risk_config_max_positions_positive"
        ),
    )
    op.create_index(
        "ix_risk_config_snapshot_effective_at", "risk_config_snapshot", ["effective_at"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_risk_config_snapshot_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'risk_config_snapshot is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER risk_config_snapshot_no_update
        BEFORE UPDATE ON risk_config_snapshot
        FOR EACH ROW EXECUTE FUNCTION reject_risk_config_snapshot_update()
        """
    )

    op.create_table(
        "trade_intent",
        sa.Column("intent_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "origin_event_id",
            sa.String(length=64),
            sa.ForeignKey("signal_decision_events.event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_row_id",
            sa.String(length=64),
            sa.ForeignKey("normalized_signals.signal_row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("entry_type", sa.String(length=10), nullable=False),
        sa.Column(
            "entry_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("stop_value", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "take_profits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("origin_event_id", name="uq_trade_intent_origin_event"),
        sa.CheckConstraint("side IN ('LONG', 'SHORT')", name="ck_trade_intent_side_valid"),
        sa.CheckConstraint(
            "entry_type IN ('MARKET', 'LIMIT', 'RANGE')",
            name="ck_trade_intent_entry_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('CREATED', 'RISK_APPROVED', 'RISK_REJECTED', 'SUBMITTED', "
            "'EXPIRED', 'CANCELLED')",
            name="ck_trade_intent_status_valid",
        ),
    )
    op.create_index("ix_trade_intent_signal_row_id", "trade_intent", ["signal_row_id"])
    op.execute(
        """
        CREATE FUNCTION reject_trade_intent_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'trade_intent is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trade_intent_no_update
        BEFORE UPDATE ON trade_intent
        FOR EACH ROW EXECUTE FUNCTION reject_trade_intent_update()
        """
    )

    op.create_table(
        "risk_decision",
        sa.Column("decision_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "intent_id",
            sa.String(length=64),
            sa.ForeignKey("trade_intent.intent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(length=10), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("computed_stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("entry_price_used", sa.Numeric(20, 8), nullable=True),
        sa.Column("market_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("market_price_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("equity_used", sa.Numeric(20, 8), nullable=True),
        sa.Column("open_position_count_used", sa.Integer(), nullable=True),
        sa.Column("daily_realized_loss_pct_used", sa.Numeric(6, 4), nullable=True),
        sa.Column(
            "config_snapshot_id",
            sa.String(length=64),
            sa.ForeignKey("risk_config_snapshot.config_snapshot_id"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("intent_id", name="uq_risk_decision_intent"),
        sa.CheckConstraint(
            "verdict IN ('APPROVED', 'REJECTED')", name="ck_risk_decision_verdict_valid"
        ),
        sa.CheckConstraint(
            "verdict = 'APPROVED' OR jsonb_array_length(reason_codes) > 0",
            name="ck_risk_decision_reject_has_reasons",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION reject_risk_decision_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'risk_decision is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER risk_decision_no_update
        BEFORE UPDATE ON risk_decision
        FOR EACH ROW EXECUTE FUNCTION reject_risk_decision_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS risk_decision_no_update ON risk_decision")
    op.execute("DROP FUNCTION IF EXISTS reject_risk_decision_update()")
    op.drop_table("risk_decision")

    op.execute("DROP TRIGGER IF EXISTS trade_intent_no_update ON trade_intent")
    op.execute("DROP FUNCTION IF EXISTS reject_trade_intent_update()")
    op.drop_index("ix_trade_intent_signal_row_id", table_name="trade_intent")
    op.drop_table("trade_intent")

    op.execute("DROP TRIGGER IF EXISTS risk_config_snapshot_no_update ON risk_config_snapshot")
    op.execute("DROP FUNCTION IF EXISTS reject_risk_config_snapshot_update()")
    op.drop_index("ix_risk_config_snapshot_effective_at", table_name="risk_config_snapshot")
    op.drop_table("risk_config_snapshot")
