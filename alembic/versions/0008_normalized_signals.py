"""Add the normalized_signals table (Phase 4 Slice 2 -- signal lifecycle).

One immutable row per lifecycle revision of a parsed EXECUTION_SIGNAL, per
FR-009/FR-010/FR-011. `signal_row_id` is this row's own identity, derived
from `(raw_message_id, parser_version)` -- idempotent re-parsing, and a
parser_version bump appends fresh rows rather than overwriting old ones,
same convention as `normalized_content`. `signal_id` is the separate,
stable *aggregate* identity shared across every revision of "the same
trading idea": revision 0 derives it from its own raw_message_id; a
reply-linked follow-up inherits its parent's signal_id and increments
revision. `ON DELETE CASCADE` on raw_message_id means retention cleanup
deleting an expired raw row removes its derived signal revisions too, same
as normalized_content.

Also adds `signal_parse_checkpoints`: unlike normalized_content (every
input row always produces exactly one output row), a normalized_content
row may legitimately produce no normalized_signals row at all
(promotional/non-signal text), so "no output row yet" can't double as
"not yet processed" the way it does for normalize_content.py. The batch
job needs an explicit progress cursor instead.

Revision ID: 0008_normalized_signals
Revises: 0007_normalized_content
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_normalized_signals"
down_revision: str | None = "0007_normalized_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "normalized_signals",
        sa.Column("signal_row_id", sa.String(length=64), primary_key=True),
        sa.Column("signal_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("raw_message_id", sa.String(length=64), nullable=False),
        sa.Column("normalizer_version", sa.String(length=20), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=True),
        sa.Column("side", sa.String(length=10), nullable=True),
        sa.Column("entry_type", sa.String(length=10), nullable=True),
        sa.Column(
            "entry_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("stop_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_origin", sa.String(length=20), nullable=False),
        sa.Column(
            "take_profits",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "evidence_spans",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("link_method", sa.String(length=20), nullable=True),
        sa.Column("parser_version", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["raw_message_id"],
            ["telegram_message_versions.source_event_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "signal_id",
            "revision",
            "parser_version",
            name="uq_normalized_signal_id_revision",
        ),
        sa.UniqueConstraint(
            "raw_message_id",
            "parser_version",
            name="uq_normalized_signal_message_version",
        ),
        sa.CheckConstraint(
            "status IN ('NEW', 'INCOMPLETE', 'VALIDATED', 'CANCELLED', 'EXPIRED', 'SUPERSEDED')",
            name="ck_normalized_signal_status_valid",
        ),
        sa.CheckConstraint(
            "side IS NULL OR side IN ('LONG', 'SHORT')",
            name="ck_normalized_signal_side_valid",
        ),
        sa.CheckConstraint(
            "entry_type IS NULL OR entry_type IN ('MARKET', 'LIMIT', 'RANGE')",
            name="ck_normalized_signal_entry_type_valid",
        ),
        sa.CheckConstraint(
            "stop_origin IN ('AUTHOR', 'DEFAULT_ROE_30', 'NONE')",
            name="ck_normalized_signal_stop_origin_valid",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_normalized_signal_revision_non_negative"),
    )
    op.create_index("ix_normalized_signal_signal_id", "normalized_signals", ["signal_id"])
    op.create_index(
        "ix_normalized_signal_channel_topic",
        "normalized_signals",
        ["channel_id", "topic_id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_normalized_signal_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'normalized_signals is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER normalized_signals_no_update
        BEFORE UPDATE ON normalized_signals
        FOR EACH ROW EXECUTE FUNCTION reject_normalized_signal_update()
        """
    )

    op.create_table(
        "signal_parse_checkpoints",
        sa.Column("channel_id", sa.BigInteger(), primary_key=True),
        sa.Column("topic_id", sa.BigInteger(), primary_key=True, server_default=sa.text("0")),
        sa.Column("parser_version", sa.String(length=20), primary_key=True),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_raw_message_id", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("channel_id > 0", name="ck_signal_parse_checkpoint_channel_positive"),
        sa.CheckConstraint("topic_id >= 0", name="ck_signal_parse_checkpoint_topic_non_negative"),
    )


def downgrade() -> None:
    op.drop_table("signal_parse_checkpoints")
    op.execute("DROP TRIGGER IF EXISTS normalized_signals_no_update ON normalized_signals")
    op.execute("DROP FUNCTION IF EXISTS reject_normalized_signal_update()")
    op.drop_index("ix_normalized_signal_channel_topic", table_name="normalized_signals")
    op.drop_index("ix_normalized_signal_signal_id", table_name="normalized_signals")
    op.drop_table("normalized_signals")
