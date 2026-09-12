# Gate 4 Checklist

- **Phase:** Phase 4 — Signal Lifecycle / Control Bot
- **Fixed Point:** `3a89cf4`
- **Gate Verdict:** `READY` (risk-accepted) — 1 open Major item (P4-MAJOR-001, edited-message/supersede linking not implemented) carried forward, not closed; see [known-issues.md](known-issues.md)
- **User Acceptance:** `ACCEPTED` (2026-09-12)
- **Phase 5 Authorization:** `GRANTED`

| Gate Condition | Result | Evidence |
|---|---|---|
| Normalizer preserves evidence, deterministic output | PASS | `test_normalization.py`; live: 1016 messages normalized under `v2` |
| Symbol alias resolution (bare asset → quoted pair) | PASS | fixed after real-data spot-check; 475/482 `VALID` for bonnie-blockchain |
| Dynamic symbol scope fails closed (no market-data client yet) | PASS | `BINANCE_USDM_ACTIVE_PERPETUAL` always yields `PENDING_MARKET_DATA`, never a guessed match |
| EXECUTION_SIGNAL parser: symbol/side/entry/SL/TP/cancel/negation with evidence spans | PASS | `test_signal_parser.py`; live: 172 real signals parsed for `@followgerry` |
| Missing symbol/side → `INCOMPLETE`, never guessed | PASS | live: 92/172 real signals correctly `INCOMPLETE` |
| Missing SL → `DEFAULT_ROE_30`, never author-price guessed; wrong-side SL rejected | PASS (wrong-side case synthetic-only, no real example yet) | `test_signal_parser.py` |
| Follow-up linking only on unambiguous pairing (reply-scoped) | **PARTIAL** | reply-based cancel/SL-TP-update linking is correct and tested; **edited messages are not linked at all** — see P4-MAJOR-001 below |
| `channel_type` gate restricts parsing to `EXECUTION_SIGNAL` channels | PASS | bonnie-blockchain (`ANALYSIS`) produces 0 signal rows, confirmed live |
| Control Bot: allowlist enforcement, 100% rejection of unauthorized requests | PASS | unit tests + live: real unauthorized sender silently rejected |
| Control Bot: double confirmation for `close_all` | PASS | nonce+window tests (valid/expired/wrong-nonce) |
| Control Bot: decision validity (unexpired, current revision, idempotent) | PASS | integration tests for `REJECTED_STALE`/`REJECTED_EXPIRED`/idempotent redelivery; live: real expired signal rejected, real fresh signal approved |
| Control Bot: approval only records a decision, no execution occurs | PASS | live-verified: `ZEC` approved, no order-related code path exists to invoke |
| All new tables append-only with cascade delete to retention cleanup | PASS | trigger + cascade tests for all 4 new tables |
| Critical = 0, Major = 0 | **NOT MET, risk-accepted** | Critical 0, Major 1 (P4-MAJOR-001) — user chose to carry it forward rather than block on it, see below |
| User acceptance recorded | RECORDED | see User Acceptance Record below |

## Open Item Blocking READY

**P4-MAJOR-001 — Edited-message and "supersede" lifecycle linking is not implemented.** Editing a Telegram message does not change `reply_to_message_id`, and the parser's follow-up linking (`_find_parent`) only ever looks at that field. An edited signal message is therefore either treated as a brand-new, unrelated signal (if it still reads as an attempt) or silently produces no update at all (if it no longer does) — the original signal's status is left unchanged either way. This is a direct, unmet requirement from the Gate 4 test-strategy's explicit "Negation, cancel, edit, supersede and expiry" list. Full detail in [known-issues.md](known-issues.md) P4-MAJOR-001.

This differs from Phase 3's P3-MAJOR-004 in one important way: that item was **structurally impossible** to build in Phase 3 (no Phase 5 code existed yet to enforce against). This item is **buildable now**, within Phase 4's own scope — the parser and lifecycle machinery already exist; what's missing is detecting a Telegram edit event and linking it the same way a reply is linked.

## Path to READY

Three honest options, in order of how much they actually close the gap:

1. **Build it now, as a Phase 4 continuation (Slice 4)** — detect an edit (the collector already persists edited messages as a new version of the same `raw_message_id` lineage; the parser would need to look up whether that `raw_message_id` already produced a signal and link to it instead of treating the edit as independent), extend `_find_parent` or add a parallel edit-aware path, add fixtures/tests, re-verify, and only then draft this Gate package again with a clean `Major = 0`.
2. **Risk-accept it as an open Major**, same pattern as [Phase 2's inherited gaps](../phase-2/gate-2-checklist.md) — mark Gate 4 `READY` with `Major = 1` explicitly on the record, carried forward as a mandatory item to close before Phase 5 (or before Production Gate 8, whichever the user judges more appropriate) rather than silently dropped.
3. **Reclassify as structurally deferred**, Phase 3's pattern — **not recommended here**, because unlike P3-MAJOR-004 this item is not structurally blocked; deferring it under that label would understate that it's simply unbuilt, not unbuildable.

The user chose option 2.

Ahead of the decision, the user asked under what real circumstances an edited message actually occurs (typo fixes, in-place SL/TP updates, cancellation-by-edit, or a channel using one message as a running status log), and whether either onboarded channel has actually produced one yet. No edited message has been observed in either channel's real backlog to date (verifiable via `SELECT channel_id, topic_id, raw_message_id, COUNT(*) FROM telegram_message_versions GROUP BY 1,2,3 HAVING COUNT(*) > 1` returning zero rows) — this gap is a real, buildable requirement, but not one that has manifested in practice for `@followgerry` or bonnie-blockchain so far.

## User Acceptance Record

- **Date:** 2026-09-12
- **Decision:** Option 2 — mark Gate 4 `READY`, risk-accept P4-MAJOR-001 as an open Major item rather than block on it.
- **User's own words:** "不太需要為了編輯訊息而卡住，如果其他的phase4的功能都正常就開始規劃phase5功能" (no need to get stuck on the edited-message issue — if everything else in Phase 4 works normally, start planning Phase 5).
- **Binding consequence:** P4-MAJOR-001 is not forgiven — it remains an open Major item, carried forward as a mandatory item to close before Production Gate 8 (or sooner, at the user's discretion), tracked in [known-issues.md](known-issues.md). If either onboarded channel is ever observed to produce an edited signal message, this gap becomes immediately relevant and should be prioritized ahead of its current low-observed-frequency status.
- **Phase 5 Authorization:** `GRANTED` as of this record, per [ADR-0001](../adr/0001-sequential-stage-gates.md). Phase 3's P3-MAJOR-004 (structurally deferred to Phase 5's own Definition-of-Done) and Phase 2's 5 inherited risk-accepted gaps (see [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md)) remain open and carried forward unchanged — this acceptance does not resolve them.
