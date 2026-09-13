from __future__ import annotations

import pytest

from telegram_trader.ai_authorization import (
    AiContentAuthorizationSnapshot,
    evaluate_channel_ai_authorization,
    evaluate_message_ai_processing,
)

AUTHORIZATIONS = ("UNKNOWN", "PENDING", "GRANTED", "REVOKED")
CHANNEL_TYPES = ("ANALYSIS", "EXECUTION_SIGNAL")


def _snapshot(
    *,
    channel_type: str = "ANALYSIS",
    ai_authorization: str = "GRANTED",
    media_authorization: str = "GRANTED",
) -> AiContentAuthorizationSnapshot:
    return AiContentAuthorizationSnapshot(
        channel_id=1,
        topic_id=0,
        channel_type=channel_type,
        ai_authorization=ai_authorization,
        media_authorization=media_authorization,
    )


def test_evaluate_channel_ai_authorization_rejects_unregistered_target() -> None:
    decision = evaluate_channel_ai_authorization(None)

    assert decision.allowed is False
    assert decision.reason is not None and "unregistered" in decision.reason


@pytest.mark.parametrize("channel_type", CHANNEL_TYPES)
@pytest.mark.parametrize("ai_authorization", AUTHORIZATIONS)
def test_evaluate_channel_ai_authorization_requires_analysis_and_granted(
    channel_type: str, ai_authorization: str
) -> None:
    decision = evaluate_channel_ai_authorization(
        _snapshot(channel_type=channel_type, ai_authorization=ai_authorization)
    )

    expected = channel_type == "ANALYSIS" and ai_authorization == "GRANTED"
    assert decision.allowed is expected
    if not expected:
        assert decision.reason is not None


def test_evaluate_message_ai_processing_rejects_unregistered_target() -> None:
    decision = evaluate_message_ai_processing(None, media_review_status="NOT_APPLICABLE")

    assert decision.allowed is False


@pytest.mark.parametrize("media_review_status", ["NOT_APPLICABLE", "PENDING_MANUAL_REVIEW"])
@pytest.mark.parametrize("media_authorization", AUTHORIZATIONS)
def test_evaluate_message_ai_processing_gates_on_media_only_when_present(
    media_authorization: str, media_review_status: str
) -> None:
    decision = evaluate_message_ai_processing(
        _snapshot(media_authorization=media_authorization),
        media_review_status=media_review_status,
    )

    if media_review_status == "PENDING_MANUAL_REVIEW":
        expected = media_authorization == "GRANTED"
    else:
        expected = True
    assert decision.allowed is expected


def test_evaluate_message_ai_processing_channel_gate_runs_first() -> None:
    decision = evaluate_message_ai_processing(
        _snapshot(ai_authorization="REVOKED", media_authorization="GRANTED"),
        media_review_status="NOT_APPLICABLE",
    )

    assert decision.allowed is False
    assert decision.reason is not None and "ai_authorization" in decision.reason


def test_evaluate_message_ai_processing_allows_text_only_when_both_granted() -> None:
    decision = evaluate_message_ai_processing(
        _snapshot(ai_authorization="GRANTED", media_authorization="GRANTED"),
        media_review_status="PENDING_MANUAL_REVIEW",
    )

    assert decision.allowed is True
