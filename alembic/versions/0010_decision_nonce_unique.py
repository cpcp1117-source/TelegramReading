"""Add a UNIQUE constraint on signal_decision_requests.nonce.

Discovered as a real bug on the first live run: Telegram caps inline-button
callback data at 64 bytes, but the original encoding embedded the full
64-character request_id alongside the 32-character nonce (~112 bytes),
so every Button.inline() call raised "Too many bytes for the data" and
the Control Bot's signal-notification poll loop crash-looped forever.

The fix resolves a button tap back to its request by `nonce` alone
(random 128 bits, now `UNIQUE`-constrained) instead of carrying
`request_id` in the callback data at all. This is a separate, additive
migration rather than editing 0009 in place, since 0009 was already
applied to the real database before this bug was found.

Revision ID: 0010_decision_nonce_unique
Revises: 0009_signal_decisions
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_decision_nonce_unique"
down_revision: str | None = "0009_signal_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_signal_decision_request_nonce", "signal_decision_requests", ["nonce"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_signal_decision_request_nonce", "signal_decision_requests", type_="unique"
    )
