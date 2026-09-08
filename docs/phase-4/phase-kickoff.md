# Phase 4 Kickoff — Signal Lifecycle / Control Bot

## Status

- Phase: `Phase 4 — Signal lifecycle/control bot` (per [system-spec.md](../phase-0/system-spec.md) §14 Implementation Plan, row 4)
- Status: `KICKOFF`
- Gate 4: `NOT EVALUATED`
- Baseline: Phase 3, accepted `READY` on 2026-09-08 (see [../phase-3/gate-3-checklist.md](../phase-3/gate-3-checklist.md))
- Authorized by: User, 2026-09-08
- Active branch: not yet created

## Inherited Item From Phase 3

Per the Gate 3 User Acceptance Record, P3-MAJOR-004 (AI/media authorization enforcement) was reclassified as structurally deferred, not resolved. It converts into a **mandatory Phase 5 Definition-of-Done item**, not a Phase 4 item — Phase 4 introduces no AI processing and no media-storage-consuming code path, so there is still nothing in Phase 4 to check `ai_authorization`/`media_authorization` against. This is carried forward untouched; Phase 4 must not attempt to close it.

Phase 2's 5 originally risk-accepted gaps (24h soak, restart no-loss, live edit-version retention, full reconciliation, combined coverage) also remain open and unaddressed — see [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md).

## A Scope Correction Found While Planning (read this first)

[architecture.md §6 Phase Capability Matrix](../phase-0/architecture.md) and [test-strategy.md Gate 3](../phase-0/test-strategy.md) both originally scheduled **deterministic text/caption normalization, symbol alias resolution, and the OCR/Vision manual-review path** under Gate 3 ("Normalization/OCR" capability starts at Phase 3 in the matrix). What Phase 3 actually delivered was only the `channel_policies` registry, multi-topic collection, retention, and live policy reload — the phase-3 phase-report explicitly says the Raw→Normalized transformation itself "remains Phase 4 scope, unstarted."

This is not a violation of ADR-0001 (nothing was implemented ahead of its accepted Gate — the opposite happened: it was delayed), but it does mean **Phase 4's real scope is larger than the "Signal lifecycle/Control Bot" label alone**: the EXECUTION_SIGNAL parser cannot run against raw, un-normalized Telegram text, so the normalization layer has to be built first, inside Phase 4, before the parser can exist. Both are included below.

## Objective (per system-spec.md)

Two layers, in dependency order:

1. **Normalization** (carried over from the original Gate 3 scope): turn an authorized Raw Message into deterministic `normalized_content` — text/caption cleanup, symbol alias resolution against each channel's declared `symbol_scope_mode`, and a manual-review flag for image-derived content (never auto-processed).
2. **Signal lifecycle + Control Bot** (the phase's own named scope): a deterministic `EXECUTION_SIGNAL` parser that turns normalized content into a `normalized_signal` with an explicit lifecycle state, plus a private, allowlisted Telegram Control Bot that surfaces those signals to the user and records manual approve/reject decisions.

This phase does **not** build `TradeIntent` submission, does **not** call any Binance endpoint (not even public market data — that starts Phase 5 per the capability matrix), and does **not** touch AI/ANALYSIS content (Phase 5).

## In Scope (proposed, pending user confirmation)

### 1. Normalization layer
- `normalized_content` table/model: deterministic text/caption normalization from `telegram_message_versions`, preserving raw evidence alongside the normalized form (FR-007).
- Symbol alias resolution that actually **consumes** `channel_policies.symbol_scope_mode`/`allowed_symbols`/`prohibited_symbols` (captured in Phase 3 but not yet enforced — this closes P3-REQ-009, currently `PARTIAL`).
- A manual-review flag/path for image-only or image-referenced content (BR-003: image-derived trade instructions must always go to a human, never auto-execute) — this phase only needs the flagging mechanism, not an actual OCR/Vision engine.
- Deterministic replay: same fixture + same normalization config/version must byte-equal the same output (NFR-010).

### 2. EXECUTION_SIGNAL parser and lifecycle
- Extract `symbol`, `side`, `entry`, `SL`, `TP`, `cancel`/`close`, and negation, each backed by an evidence span into the source text (FR-009).
- Missing `symbol` or `side` → `INCOMPLETE`; missing `SL` → `DEFAULT_ROE_30`, never guess the author's intended price (FR-010, BR-004).
- Signal state machine per [logical-data-model.md §4](../phase-0/logical-data-model.md): `NEW → INCOMPLETE/VALIDATED → CANCELLED/EXPIRED/SUPERSEDED`.
- Follow-up matching only via reply, message link, explicit signal ID, or an otherwise-unique lifecycle match; anything ambiguous stays manual, never guessed by same-symbol proximity (FR-011).

### 3. Control Bot (new component, new credential)
- Separate Telegram **Bot API** client (not the existing MTProto user session) — a new `TELEGRAM_BOT_TOKEN`, created by the user via @BotFather and supplied the same way prior credentials were: local, git-ignored `.env`, never pasted into chat.
- Single numeric allowlisted user ID; any other sender is rejected (NFR-007, target rejection rate 100%).
- Status/notification commands: new signal, lifecycle transitions.
- Manual approve/reject recording for `VALIDATED` signals — per BR-002, `EXECUTION_SIGNAL` only becomes eligible for *automatic* action after the Production Gate (Gate 8), so before that, every validated signal still needs an explicit human decision. Phase 4 records that decision; it does not act on it (no Risk Engine/Execution Gateway exist until Phase 6).
- Command nonce + expiry, matching the Gate 4 test-strategy requirement ("command nonce, expired approval, double confirmation").

## Explicitly Out of Scope / Prohibited

- No `TradeIntent` **submission** — see open question below on whether the record itself should exist yet.
- No Binance API of any kind, including public market data (per the capability matrix, that starts Phase 5).
- No AI/ANALYSIS content extraction, no Strategy Contract, no Candidate Trade (Phase 5).
- No Risk Engine, no position sizing, no Protection Order (Phase 6).
- No new channel onboarding — still just the 2 channels already accepted, per the user's standing instruction.
- No closing of P3-MAJOR-004 (AI/media authorization) — nothing in Phase 4 consumes those columns either.

## Open Question — Does a `TradeIntent` record get created in Phase 4?

The Gate 4 test-strategy line "Duplicate lifecycle cannot create two intents" implies some intent-shaped record exists by Gate 4. But architecture.md introduces `risk-engine` (the only consumer of `TradeIntentRequested.v1`) starting Phase 6, and Phase 3 already drew criticism (P3-MAJOR-004) for shipping columns nothing consumes yet.

**Recommendation:** stop the Phase 4 data model at a `VALIDATED` (or `INCOMPLETE`/`CANCELLED`/etc.) `normalized_signal` plus the Control Bot's recorded approve/reject decision. Do **not** create a `trade_intent` row until Phase 6, when the Risk Engine exists to actually consume it — avoids repeating an inert-column pattern, and "duplicate lifecycle" idempotency can be tested against the signal lifecycle itself (e.g., replaying the same Telegram edit must not produce two `VALIDATED` signals) without needing a `TradeIntent` table yet.

This needs the user's explicit confirmation before implementation starts, since it's a data-model scope decision, not just an implementation detail.

## Current Decision

Phase 4 kickoff scope is drafted above, pending user confirmation on (a) the overall in-scope list and (b) the `TradeIntent` timing question. No branch created, no code written yet.
