from __future__ import annotations

from dataclasses import dataclass

from telegram_trader.channel_policy import PolicyDecision


@dataclass(frozen=True, slots=True)
class AiContentAuthorizationSnapshot:
    """A narrow, AI-processing-focused view of one `channel_policies` row.

    Deliberately separate from `channel_policy.ChannelPolicySnapshot` (raw
    collection gating) and `normalization.ChannelSymbolPolicy` (symbol
    scope) -- same "each consumer gets its own minimal view" convention.
    """

    channel_id: int
    topic_id: int
    channel_type: str
    ai_authorization: str
    media_authorization: str


def evaluate_channel_ai_authorization(
    policy: AiContentAuthorizationSnapshot | None,
) -> PolicyDecision:
    """Coarse, per-(channel,topic) gate: may ANY content from this target reach the LLM?

    Does not evaluate a specific message's media status -- see
    `evaluate_message_ai_processing` for the per-row check made immediately
    before an actual OpenAI call.
    """
    if policy is None:
        return PolicyDecision(False, "no channel_policies row (unregistered channel)")
    if policy.channel_type != "ANALYSIS":
        return PolicyDecision(False, f"channel_type={policy.channel_type!r} is not ANALYSIS")
    if policy.ai_authorization != "GRANTED":
        return PolicyDecision(False, f"ai_authorization={policy.ai_authorization!r}")
    return PolicyDecision(True)


def evaluate_message_ai_processing(
    policy: AiContentAuthorizationSnapshot | None,
    *,
    media_review_status: str,
) -> PolicyDecision:
    """Per-message gate, checked immediately before the OpenAI call for that row.

    Only `normalized_text` is ever sent to the LLM -- the chart image itself
    is never read or uploaded (BR-003). Even so, a message whose
    `media_review_status` is `PENDING_MANUAL_REVIEW` (it has attached,
    unreviewed media) additionally requires `media_authorization=GRANTED`,
    not just `ai_authorization` -- the more conservative reading, confirmed
    with the user, since bonnie-blockchain's content is authored as one
    caption+chart unit rather than separable text/image parts.
    """
    channel_decision = evaluate_channel_ai_authorization(policy)
    if not channel_decision.allowed:
        return channel_decision
    assert policy is not None  # narrowed by evaluate_channel_ai_authorization above
    if media_review_status == "PENDING_MANUAL_REVIEW" and policy.media_authorization != "GRANTED":
        return PolicyDecision(
            False,
            f"media present (media_review_status={media_review_status!r}) but "
            f"media_authorization={policy.media_authorization!r}",
        )
    return PolicyDecision(True)
