"""Add the channel_policies table and seed the two known real channels.

`topic_id=0` is the "whole channel, no forum topic" sentinel, same
convention as `telegram_collector_checkpoints`. The seed values are taken
verbatim from the corresponding onboarding docs under docs/phase-0/channels/
as of this migration's authoring date; that markdown remains the
human-readable evidence record, and this table is the runtime source of
truth once applied.

Revision ID: 0005_channel_policies
Revises: 0004_telegram_topic_scope
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_channel_policies"
down_revision: str | None = "0004_telegram_topic_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

channel_policies_table = sa.table(
    "channel_policies",
    sa.column("channel_id", sa.BigInteger()),
    sa.column("topic_id", sa.BigInteger()),
    sa.column("label", sa.String()),
    sa.column("username", sa.String()),
    sa.column("channel_type", sa.String()),
    sa.column("access_authorization", sa.String()),
    sa.column("automation_authorization", sa.String()),
    sa.column("ai_authorization", sa.String()),
    sa.column("media_authorization", sa.String()),
    sa.column("symbol_scope_mode", sa.String()),
    sa.column("allowed_symbols", postgresql.JSONB()),
    sa.column("message_languages", postgresql.JSONB()),
    sa.column("supported_content_types", postgresql.JSONB()),
    sa.column("gate_decision", sa.String()),
    sa.column("onboarding_doc_path", sa.String()),
    sa.column("acceptance_owner", sa.String()),
)


def upgrade() -> None:
    op.create_table(
        "channel_policies",
        sa.Column("channel_id", sa.BigInteger(), primary_key=True),
        sa.Column("topic_id", sa.BigInteger(), primary_key=True, server_default=sa.text("0")),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("channel_type", sa.String(length=20), nullable=False),
        sa.Column(
            "access_authorization",
            sa.String(length=20),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column(
            "automation_authorization",
            sa.String(length=20),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column(
            "ai_authorization", sa.String(length=20), nullable=False, server_default="UNKNOWN"
        ),
        sa.Column(
            "media_authorization",
            sa.String(length=20),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column("symbol_scope_mode", sa.String(length=30), nullable=False),
        sa.Column(
            "allowed_symbols",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "prohibited_symbols",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "message_languages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "supported_content_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("raw_retention_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("gate_decision", sa.String(length=20), nullable=False),
        sa.Column(
            "policy_detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("onboarding_doc_path", sa.String(length=500), nullable=True),
        sa.Column("acceptance_owner", sa.String(length=200), nullable=True),
        sa.Column("acceptance_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("channel_id > 0", name="ck_channel_policy_channel_positive"),
        sa.CheckConstraint("topic_id >= 0", name="ck_channel_policy_topic_non_negative"),
        sa.CheckConstraint(
            "channel_type IN ('ANALYSIS', 'EXECUTION_SIGNAL')",
            name="ck_channel_policy_type_valid",
        ),
        sa.CheckConstraint(
            "access_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_access_auth_valid",
        ),
        sa.CheckConstraint(
            "automation_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_automation_auth_valid",
        ),
        sa.CheckConstraint(
            "ai_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_ai_auth_valid",
        ),
        sa.CheckConstraint(
            "media_authorization IN ('UNKNOWN', 'PENDING', 'GRANTED', 'REVOKED')",
            name="ck_channel_policy_media_auth_valid",
        ),
        sa.CheckConstraint(
            "symbol_scope_mode IN ('STATIC_ALLOWLIST', 'BINANCE_USDM_ACTIVE_PERPETUAL')",
            name="ck_channel_policy_symbol_scope_valid",
        ),
        sa.CheckConstraint(
            "gate_decision IN ('MONITOR_ONLY', 'ENABLED', 'PAUSED', 'REJECTED')",
            name="ck_channel_policy_gate_decision_valid",
        ),
        sa.CheckConstraint("raw_retention_days > 0", name="ck_channel_policy_retention_positive"),
    )

    op.bulk_insert(
        channel_policies_table,
        [
            {
                "channel_id": 2439599598,
                "topic_id": 0,
                "label": "Monster-貨幣宇宙中心",
                "username": "followgerry",
                "channel_type": "EXECUTION_SIGNAL",
                "access_authorization": "GRANTED",
                "automation_authorization": "GRANTED",
                "ai_authorization": "GRANTED",
                "media_authorization": "GRANTED",
                "symbol_scope_mode": "BINANCE_USDM_ACTIVE_PERPETUAL",
                "allowed_symbols": [],
                "message_languages": ["zh-TW", "en"],
                "supported_content_types": ["TEXT", "CAPTION", "IMAGE"],
                "gate_decision": "MONITOR_ONLY",
                "onboarding_doc_path": "docs/phase-0/channels/monster-currency-universe.md",
                "acceptance_owner": "User",
            },
            {
                "channel_id": 2382278102,
                "topic_id": 21,
                "label": "邦妮區塊鏈-BTC ETH 即時更新",
                "username": None,
                "channel_type": "ANALYSIS",
                "access_authorization": "GRANTED",
                "automation_authorization": "GRANTED",
                "ai_authorization": "GRANTED",
                "media_authorization": "GRANTED",
                "symbol_scope_mode": "STATIC_ALLOWLIST",
                "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
                "message_languages": ["zh-TW"],
                "supported_content_types": ["CAPTION"],
                "gate_decision": "MONITOR_ONLY",
                "onboarding_doc_path": "docs/phase-0/channels/邦妮區塊鏈.md",
                "acceptance_owner": "User",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("channel_policies")
