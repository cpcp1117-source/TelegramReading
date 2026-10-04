"""Settings changed from the Web Console, as an append-only change log.

Each row is one change to one setting (`key` = the `Settings` field name). The
value in force is the newest row per key; a row whose `value` is NULL means
"back to the .env default". Services overlay these values on their environment
settings every cycle (`runtime_settings.apply_overrides`), so a change needs no
restart for settings marked as live. Nothing is ever updated in place.

Revision ID: 0026_runtime_settings
Revises: 0025_draft_entry_and_size
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_runtime_settings"
down_revision: str | None = "0025_draft_entry_and_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_setting_changes",
        sa.Column("change_id", sa.String(length=64), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_runtime_setting_changes_key_changed_at",
        "runtime_setting_changes",
        ["key", "changed_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_runtime_setting_change_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'runtime_setting_changes is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER runtime_setting_changes_no_update_or_delete
        BEFORE UPDATE OR DELETE ON runtime_setting_changes
        FOR EACH ROW EXECUTE FUNCTION reject_runtime_setting_change_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS runtime_setting_changes_no_update_or_delete "
        "ON runtime_setting_changes"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_runtime_setting_change_mutation()")
    op.drop_index("ix_runtime_setting_changes_key_changed_at", table_name="runtime_setting_changes")
    op.drop_table("runtime_setting_changes")
