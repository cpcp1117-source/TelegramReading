from __future__ import annotations

import logging

import pytest

from telegram_trader.channel_policy import (
    ChannelPolicySnapshot,
    evaluate_raw_collection,
    filter_authorized_targets,
)
from telegram_trader.config import TelegramChannelTarget

AUTHORIZATIONS = ("UNKNOWN", "PENDING", "GRANTED", "REVOKED")
GATE_DECISIONS = ("MONITOR_ONLY", "ENABLED", "PAUSED", "REJECTED")


def snapshot(automation_authorization: str, gate_decision: str) -> ChannelPolicySnapshot:
    return ChannelPolicySnapshot(
        channel_id=1,
        topic_id=0,
        automation_authorization=automation_authorization,
        gate_decision=gate_decision,
    )


def test_evaluate_raw_collection_rejects_unregistered_target() -> None:
    decision = evaluate_raw_collection(None)

    assert decision.allowed is False
    assert decision.reason is not None and "unregistered" in decision.reason


@pytest.mark.parametrize("automation_authorization", AUTHORIZATIONS)
@pytest.mark.parametrize("gate_decision", GATE_DECISIONS)
def test_evaluate_raw_collection_allows_only_granted_and_active(
    automation_authorization: str, gate_decision: str
) -> None:
    decision = evaluate_raw_collection(snapshot(automation_authorization, gate_decision))

    expected_allowed = automation_authorization == "GRANTED" and gate_decision in (
        "MONITOR_ONLY",
        "ENABLED",
    )
    assert decision.allowed is expected_allowed
    if not expected_allowed:
        assert decision.reason is not None


def test_filter_authorized_targets_keeps_only_allowed_and_logs_excluded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    targets = [
        TelegramChannelTarget(channel_id=1, topic_id=None, username="a", label="Allowed"),
        TelegramChannelTarget(channel_id=2, topic_id=21, username=None, label="Paused"),
        TelegramChannelTarget(channel_id=3, topic_id=None, username=None, label="Unregistered"),
    ]
    policies = {
        (1, 0): ChannelPolicySnapshot(1, 0, "GRANTED", "MONITOR_ONLY"),
        (2, 21): ChannelPolicySnapshot(2, 21, "GRANTED", "PAUSED"),
    }
    logger = logging.getLogger("test-channel-policy")

    with caplog.at_level(logging.WARNING, logger="test-channel-policy"):
        authorized = filter_authorized_targets(targets, policies, logger)

    assert [target.label for target in authorized] == ["Allowed"]
    assert len(caplog.records) == 2
