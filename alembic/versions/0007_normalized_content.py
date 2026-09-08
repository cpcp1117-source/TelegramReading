"""Add the normalized_content table (Phase 4 Slice 1 -- normalization layer).

Raw Message -> deterministic NormalizedContent, per FR-007. `content_id` is
derived from `(raw_message_id, normalizer_version)`, so re-normalizing the
same message with the same version is naturally idempotent, and bumping
`normalizer_version` appends a new row rather than overwriting the old one.
`ON DELETE CASCADE` on `raw_message_id` means retention cleanup deleting an
expired `telegram_message_versions` row automatically removes its normalized
derivative too -- no separate retention job is needed for this table. A
dedicated trigger rejects UPDATE only; DELETE is otherwise left
unconditionally allowed here since the only deletion path is that FK
cascade, which is already access-controlled by the existing retention
trigger on the parent table.

Revision ID: 0007_normalized_content
Revises: 0006_retention_cleanup
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_normalized_content"
down_revision: str | None = "0006_retention_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "normalized_content",
        sa.Column("content_id", sa.String(length=64), primary_key=True),
        sa.Column("raw_message_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("normalizer_version", sa.String(length=20), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("symbol_scope_mode", sa.String(length=30), nullable=False),
        sa.Column(
            "symbol_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "resolved_symbols",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "media_review_status",
            sa.String(length=30),
            nullable=False,
            server_default="NOT_APPLICABLE",
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            "raw_message_id",
            "normalizer_version",
            name="uq_normalized_content_message_version",
        ),
        sa.CheckConstraint(
            "symbol_scope_mode IN ('STATIC_ALLOWLIST', 'BINANCE_USDM_ACTIVE_PERPETUAL')",
            name="ck_normalized_content_symbol_scope_valid",
        ),
        sa.CheckConstraint(
            "media_review_status IN ('NOT_APPLICABLE', 'PENDING_MANUAL_REVIEW')",
            name="ck_normalized_content_media_review_valid",
        ),
    )
    op.create_index(
        "ix_normalized_content_channel_topic",
        "normalized_content",
        ["channel_id", "topic_id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_normalized_content_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'normalized_content is append-only (update not permitted)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER normalized_content_no_update
        BEFORE UPDATE ON normalized_content
        FOR EACH ROW EXECUTE FUNCTION reject_normalized_content_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS normalized_content_no_update ON normalized_content")
    op.execute("DROP FUNCTION IF EXISTS reject_normalized_content_update()")
    op.drop_index("ix_normalized_content_channel_topic", table_name="normalized_content")
    op.drop_table("normalized_content")
