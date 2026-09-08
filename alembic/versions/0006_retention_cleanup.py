"""Allow a dedicated retention-cleanup path to delete aged telegram_message_versions rows.

Replaces `reject_telegram_message_version_mutation()` (same trigger, no
trigger DDL change) so a DELETE is allowed only when both: (a) the session
set a transaction-scoped `SET LOCAL app.retention_cleanup = 'on'`, and
(b) the row being deleted is actually past its channel's declared
`channel_policies.raw_retention_days` (looked up server-side inside the
trigger). This means even a compromised app credential that sets the GUC
and issues an unscoped DELETE cannot remove non-expired rows — the database
enforces the real age invariant, not just a flag. UPDATE stays
unconditionally rejected; nothing needs it.

`SET LOCAL`, never bare `SET`, is mandatory given connection pooling — a
bare `SET` would persist the GUC on the physical connection past this
transaction.

Revision ID: 0006_retention_cleanup
Revises: 0005_channel_policies
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_retention_cleanup"
down_revision: str | None = "0005_channel_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENFORCED_FUNCTION = """
    CREATE OR REPLACE FUNCTION reject_telegram_message_version_mutation()
    RETURNS trigger AS $$
    DECLARE
        retention_days integer;
    BEGIN
        IF TG_OP = 'DELETE'
           AND coalesce(current_setting('app.retention_cleanup', true), 'off') = 'on'
        THEN
            SELECT raw_retention_days INTO retention_days
            FROM channel_policies
            WHERE channel_id = OLD.channel_id
              AND topic_id = coalesce(OLD.topic_id, 0);

            IF retention_days IS NOT NULL
               AND OLD.received_at < (now() - (retention_days || ' days')::interval)
            THEN
                RETURN OLD;
            END IF;
        END IF;
        RAISE EXCEPTION 'telegram_message_versions is append-only
            (row not past retention, or app.retention_cleanup not set)';
    END;
    $$ LANGUAGE plpgsql
"""

_ORIGINAL_FUNCTION = """
    CREATE OR REPLACE FUNCTION reject_telegram_message_version_mutation()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'telegram_message_versions is append-only';
    END;
    $$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute(_ENFORCED_FUNCTION)


def downgrade() -> None:
    op.execute(_ORIGINAL_FUNCTION)
