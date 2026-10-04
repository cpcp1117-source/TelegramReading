"""Analysis setups (AI-extracted, code-monitored trade ideas) + the Bonnie VIP channel policy.

`analysis_setups` holds one monitorable idea extracted from an ANALYSIS-channel message
(symbol, side, entry zone, stop, targets, how to confirm). `analysis_setup_checkpoints`
is the extraction cursor (same rationale as `thesis_extraction_checkpoints`). The
channel policy row onboards `邦妮區塊鏈 VIP專屬CLUB` topic 7 (self-declared authorization,
user request 2026-10-04) as an ANALYSIS channel over all active USD-M perpetuals.

Revision ID: 0021_analysis_setups
Revises: 0020_algo_order_statuses
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_analysis_setups"
down_revision: str | None = "0020_algo_order_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIP_CHANNEL_ID = 3182714504
_VIP_TOPIC_ID = 7

_channel_policies_table = sa.table(
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
        "analysis_setups",
        sa.Column("setup_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "content_id",
            sa.String(length=64),
            sa.ForeignKey("normalized_content.content_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "raw_message_id",
            sa.String(length=64),
            sa.ForeignKey("telegram_message_versions.source_event_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("extraction_schema_version", sa.String(length=20), nullable=False),
        sa.Column("llm_model", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("entry_zone_low", sa.Numeric(20, 8), nullable=False),
        sa.Column("entry_zone_high", sa.Numeric(20, 8), nullable=False),
        sa.Column("confirm_rule", sa.String(length=10), nullable=False),
        sa.Column("trigger_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_value", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "take_profits", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("cancel_if_target_first", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.String(length=10), nullable=False),
        sa.Column("summary_zh", sa.Text(), nullable=False),
        sa.Column("risk_notes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "evidence_quotes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.String(length=40), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("touched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signal_id", sa.String(length=64), nullable=True),
        sa.Column("raw_llm_response", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("side IN ('LONG', 'SHORT')", name="ck_analysis_setup_side_valid"),
        sa.CheckConstraint(
            "status IN ('WATCHING', 'ZONE_TOUCHED', 'TRIGGERED', 'INVALIDATED', 'EXPIRED', "
            "'SUPERSEDED')",
            name="ck_analysis_setup_status_valid",
        ),
        sa.CheckConstraint(
            "confirm_rule IN ('REJECT', 'BREAK')", name="ck_analysis_setup_confirm_rule_valid"
        ),
        sa.CheckConstraint(
            "confidence IN ('HIGH', 'MEDIUM', 'LOW')", name="ck_analysis_setup_confidence_valid"
        ),
        sa.CheckConstraint(
            "entry_zone_low <= entry_zone_high", name="ck_analysis_setup_zone_ordered"
        ),
    )
    op.create_index("ix_analysis_setups_status", "analysis_setups", ["status"])
    op.create_index("ix_analysis_setups_symbol", "analysis_setups", ["symbol"])

    op.create_table(
        "analysis_setup_checkpoints",
        sa.Column("channel_id", sa.BigInteger(), primary_key=True),
        sa.Column("topic_id", sa.BigInteger(), primary_key=True, server_default=sa.text("0")),
        sa.Column("extraction_schema_version", sa.String(length=20), primary_key=True),
        sa.Column("llm_model", sa.String(length=100), primary_key=True),
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_content_id", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("channel_id > 0", name="ck_analysis_setup_checkpoint_channel_positive"),
        sa.CheckConstraint("topic_id >= 0", name="ck_analysis_setup_checkpoint_topic_non_negative"),
    )

    op.bulk_insert(
        _channel_policies_table,
        [
            {
                "channel_id": _VIP_CHANNEL_ID,
                "topic_id": _VIP_TOPIC_ID,
                "label": "邦妮區塊鏈 VIP專屬CLUB (幣種分析)",
                "username": None,
                "channel_type": "ANALYSIS",
                "access_authorization": "GRANTED",
                "automation_authorization": "GRANTED",
                "ai_authorization": "GRANTED",
                "media_authorization": "GRANTED",
                "symbol_scope_mode": "BINANCE_USDM_ACTIVE_PERPETUAL",
                "allowed_symbols": [],
                "message_languages": ["zh-TW"],
                "supported_content_types": ["TEXT", "CAPTION", "IMAGE"],
                "gate_decision": "MONITOR_ONLY",
                "onboarding_doc_path": "docs/phase-0/channels/bonnie-vip-club.md",
                "acceptance_owner": "User",
            }
        ],
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM channel_policies WHERE channel_id = {_VIP_CHANNEL_ID} "
        f"AND topic_id = {_VIP_TOPIC_ID}"
    )
    op.drop_table("analysis_setup_checkpoints")
    op.drop_index("ix_analysis_setups_symbol", table_name="analysis_setups")
    op.drop_index("ix_analysis_setups_status", table_name="analysis_setups")
    op.drop_table("analysis_setups")
