"""Mark pre-existing execution notifications as already reminded (Phase 6).

Per explicit user request 2026-09-29: a failure notification the user has
not marked handled within 5 minutes is sent once more. That state lives in
`outbox_delivery_receipts` under the `control_bot_reminder` /
`control_bot_handled` consumer names -- no schema change. Without this
backfill, every execution event delivered before the feature existed (e.g.
the 2026-09-28 live run's `execution_unexpected_error`) would look "delivered
long ago, never handled" and be re-sent the moment Control Bot restarts.

Downgrade removes only the receipts this migration inserted.

Revision ID: 0019_failure_reminder_backfill
Revises: 0018_take_profit_notify
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_failure_reminder_backfill"
down_revision: str | None = "0018_take_profit_notify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_CUTOFF = "2026-09-29 00:00:00+00"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO outbox_delivery_receipts (consumer_name, event_id, delivered_at)
        SELECT 'control_bot_reminder', event_id, TIMESTAMPTZ '{_BACKFILL_CUTOFF}'
        FROM outbox_events
        WHERE event_type LIKE 'execution\\_%'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM outbox_delivery_receipts
        WHERE consumer_name = 'control_bot_reminder'
          AND delivered_at = TIMESTAMPTZ '{_BACKFILL_CUTOFF}'
        """
    )
