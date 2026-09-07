"""Add forum-topic scoping to the Telegram collector tables.

`telegram_message_versions.topic_id` is nullable: NULL means "not a forum
topic" (e.g. the existing `@followgerry` broadcast channel rows).
`telegram_collector_checkpoints.topic_id` cannot be NULL because it is part
of the primary key; `0` is its sentinel for "whole channel, no topic" —
a deliberately different convention from the nullable column above.

Revision ID: 0004_telegram_topic_scope
Revises: 0003_telegram_readonly_collector
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_telegram_topic_scope"
down_revision: str | None = "0003_telegram_readonly_collector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_collector_checkpoints",
        sa.Column("topic_id", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.drop_constraint(
        "telegram_collector_checkpoints_pkey", "telegram_collector_checkpoints", type_="primary"
    )
    op.create_primary_key(
        "telegram_collector_checkpoints_pkey",
        "telegram_collector_checkpoints",
        ["channel_id", "topic_id"],
    )
    op.create_check_constraint(
        "ck_telegram_checkpoint_topic_non_negative",
        "telegram_collector_checkpoints",
        "topic_id >= 0",
    )

    op.add_column(
        "telegram_message_versions",
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_telegram_topic_positive",
        "telegram_message_versions",
        "topic_id IS NULL OR topic_id > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_telegram_topic_positive", "telegram_message_versions", type_="check")
    op.drop_column("telegram_message_versions", "topic_id")

    op.drop_constraint(
        "ck_telegram_checkpoint_topic_non_negative",
        "telegram_collector_checkpoints",
        type_="check",
    )
    op.drop_constraint(
        "telegram_collector_checkpoints_pkey", "telegram_collector_checkpoints", type_="primary"
    )
    op.create_primary_key(
        "telegram_collector_checkpoints_pkey", "telegram_collector_checkpoints", ["channel_id"]
    )
    op.drop_column("telegram_collector_checkpoints", "topic_id")
