"""Add the thesis table (Phase 5 Slice 2a -- Thesis extraction).

Turns an authorized ANALYSIS-channel `normalized_content` row into a
schema-valid `thesis` row via OpenAI (FR-012). `thesis_row_id` is derived
from `(content_id, extraction_schema_version, llm_model)`, so re-extraction
under the same version/model is idempotent, and a prompt/schema/model
change appends fresh rows instead of overwriting old ones -- same
convention as `normalized_content`/`normalized_signals`. `thesis_id` is a
separate, stable aggregate identity a later Strategy Contract/Market
Confirmation slice will reuse across lifecycle revisions; this slice
always writes `revision=0`. `status`'s CHECK is deliberately narrower than
the full CONTEXT.md lifecycle (only `DRAFT`/`INSUFFICIENT_DATA`) since this
slice never produces the other four states -- widened by a later additive
migration when that logic actually exists, not pre-declared now.
`ON DELETE CASCADE` on `content_id` means retention cleanup purging the
underlying raw/normalized content removes the thesis too, same chain as
every other derived table.

Also adds `thesis_extraction_checkpoints`, mirroring `signal_parse_checkpoints`:
a `normalized_content` row may legitimately produce no `thesis` row at all
(unauthorized, no resolvable symbol, or a provider/validation failure), so
"row exists" idempotency alone would re-select the same skipped rows
forever without a separate progress cursor.

Revision ID: 0012_thesis
Revises: 0011_binance_symbol_snapshots
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_thesis"
down_revision: str | None = "0011_binance_symbol_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "thesis",
        sa.Column("thesis_row_id", sa.String(length=64), primary_key=True),
        sa.Column("thesis_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("extraction_schema_version", sa.String(length=20), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=False),
        sa.Column("llm_schema_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("primary_direction", sa.String(length=10), nullable=False),
        sa.Column("confidence_status", sa.String(length=20), nullable=False),
        sa.Column(
            "evidence_quotes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("source_text_included", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "media_included",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "raw_llm_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["normalized_content.content_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "content_id",
            "extraction_schema_version",
            "llm_model",
            name="uq_thesis_content_extraction",
        ),
        sa.UniqueConstraint(
            "thesis_id",
            "revision",
            "extraction_schema_version",
            "llm_model",
            name="uq_thesis_id_revision",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'INSUFFICIENT_DATA')",
            name="ck_thesis_status_valid",
        ),
        sa.CheckConstraint(
            "primary_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')",
            name="ck_thesis_primary_direction_valid",
        ),
        sa.CheckConstraint(
            "confidence_status IN ('HIGH', 'MEDIUM', 'LOW', 'INSUFFICIENT_DATA')",
            name="ck_thesis_confidence_status_valid",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_thesis_revision_non_negative"),
    )
    op.create_index("ix_thesis_channel_topic", "thesis", ["channel_id", "topic_id"])
    op.create_index("ix_thesis_thesis_id", "thesis", ["thesis_id"])
    op.create_table(
        "thesis_extraction_checkpoints",
        sa.Column("channel_id", sa.BigInteger(), primary_key=True),
        sa.Column("topic_id", sa.BigInteger(), primary_key=True, server_default="0"),
        sa.Column("extraction_schema_version", sa.String(length=20), primary_key=True),
        sa.Column("llm_model", sa.String(length=100), primary_key=True),
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_content_id", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "channel_id > 0", name="ck_thesis_extraction_checkpoint_channel_positive"
        ),
        sa.CheckConstraint(
            "topic_id >= 0", name="ck_thesis_extraction_checkpoint_topic_non_negative"
        ),
    )
    op.execute(
        """
        CREATE FUNCTION reject_thesis_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'thesis is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER thesis_no_update
        BEFORE UPDATE ON thesis
        FOR EACH ROW EXECUTE FUNCTION reject_thesis_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS thesis_no_update ON thesis")
    op.execute("DROP FUNCTION IF EXISTS reject_thesis_update()")
    op.drop_table("thesis_extraction_checkpoints")
    op.drop_index("ix_thesis_thesis_id", table_name="thesis")
    op.drop_index("ix_thesis_channel_topic", table_name="thesis")
    op.drop_table("thesis")
