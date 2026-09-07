from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from telegram_trader.config import TelegramChannelTarget
from telegram_trader.models import ChannelPolicy

_ALLOWED_GATE_DECISIONS = frozenset({"MONITOR_ONLY", "ENABLED"})


class ChannelPolicyError(RuntimeError):
    """Raised when no configured target remains authorized for raw collection."""


@dataclass(frozen=True, slots=True)
class ChannelPolicySnapshot:
    channel_id: int
    topic_id: int
    automation_authorization: str
    gate_decision: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


def _policy_key(channel_id: int, topic_id: int | None) -> tuple[int, int]:
    """`channel_policies.topic_id` uses `0` as its "no topic" sentinel."""
    return (channel_id, topic_id or 0)


def evaluate_raw_collection(policy: ChannelPolicySnapshot | None) -> PolicyDecision:
    """Pure gate check: is this target authorized for raw (read-only) collection?

    Does not evaluate AI processing, media storage, or symbol scope — those
    are Phase 4/5 concerns read from the same table's other columns.
    """
    if policy is None:
        return PolicyDecision(False, "no channel_policies row (unregistered target)")
    if policy.automation_authorization != "GRANTED":
        return PolicyDecision(
            False, f"automation_authorization={policy.automation_authorization!r}"
        )
    if policy.gate_decision not in _ALLOWED_GATE_DECISIONS:
        return PolicyDecision(False, f"gate_decision={policy.gate_decision!r}")
    return PolicyDecision(True)


def load_channel_policies(
    session: Session, targets: list[TelegramChannelTarget]
) -> dict[tuple[int, int], ChannelPolicySnapshot]:
    keys = {_policy_key(target.channel_id, target.topic_id) for target in targets}
    if not keys:
        return {}
    channel_ids = {channel_id for channel_id, _ in keys}
    rows = session.scalars(select(ChannelPolicy).where(ChannelPolicy.channel_id.in_(channel_ids)))
    return {
        (row.channel_id, row.topic_id): ChannelPolicySnapshot(
            channel_id=row.channel_id,
            topic_id=row.topic_id,
            automation_authorization=row.automation_authorization,
            gate_decision=row.gate_decision,
        )
        for row in rows
        if (row.channel_id, row.topic_id) in keys
    }


def filter_authorized_targets(
    targets: list[TelegramChannelTarget],
    policies: dict[tuple[int, int], ChannelPolicySnapshot],
    logger: logging.Logger,
) -> list[TelegramChannelTarget]:
    """Keep only targets whose Channel Policy authorizes raw collection.

    One unauthorized/unregistered/paused target is logged and skipped rather
    than aborting the whole process, so it doesn't take down collection for
    an otherwise-authorized target.
    """
    authorized: list[TelegramChannelTarget] = []
    for target in targets:
        policy = policies.get(_policy_key(target.channel_id, target.topic_id))
        decision = evaluate_raw_collection(policy)
        if decision.allowed:
            authorized.append(target)
        else:
            logger.warning(
                "channel policy excludes target from raw collection",
                extra={
                    "context": {
                        "channel_id": target.channel_id,
                        "topic_id": target.topic_id,
                        "label": target.label,
                        "reason": decision.reason,
                    }
                },
            )
    return authorized
