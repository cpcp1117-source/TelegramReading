"""Allow Binance's algo-order statuses TRIGGERING/FINISHED in exchange_order.status (Phase 6).

`exchange_order` also holds the stop-loss/take-profit algo orders, whose status
column carries Binance's `algoStatus`. That vocabulary includes `TRIGGERING` and
`FINISHED` (a triggered conditional order whose market order completed), which
the order-status CHECK from migration 0017 did not allow -- found by the position
monitor's integration test (2026-09-29) writing back a triggered stop-loss.

Revision ID: 0020_algo_order_statuses
Revises: 0019_failure_reminder_backfill
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_algo_order_statuses"
down_revision: str | None = "0019_failure_reminder_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUSES = (
    "'NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'PENDING_CANCEL', 'REJECTED', "
    "'EXPIRED', 'EXPIRED_IN_MATCH', 'TRIGGERED', 'UNKNOWN'"
)
_NEW_STATUSES = f"{_OLD_STATUSES}, 'TRIGGERING', 'FINISHED'"


def upgrade() -> None:
    op.drop_constraint("ck_exchange_order_status_valid", "exchange_order", type_="check")
    op.create_check_constraint(
        "ck_exchange_order_status_valid", "exchange_order", f"status IN ({_NEW_STATUSES})"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE exchange_order SET status = 'TRIGGERED' WHERE status IN ('TRIGGERING', 'FINISHED')"
    )
    op.drop_constraint("ck_exchange_order_status_valid", "exchange_order", type_="check")
    op.create_check_constraint(
        "ck_exchange_order_status_valid", "exchange_order", f"status IN ({_OLD_STATUSES})"
    )
