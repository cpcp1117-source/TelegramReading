# Phase 3 Requirement Traceability

- **Source:** [system-spec.md](../phase-0/system-spec.md) FR-002/FR-005/FR-008/BR-001, [phase-kickoff.md](phase-kickoff.md) In Scope list
- **Fixed Point:** `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813`

| ID | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| P3-REQ-001 | Channel Policy allowlist controls type/symbol-scope/authorization/retention per source | `ChannelPolicy` model (`models.py`), migration 0005 | integration round-trip test; live query on real DB | PASS |
| P3-REQ-002 | Unregistered channel fails closed | `evaluate_raw_collection` returns not-allowed for `policy is None` | `test_channel_policy.py::test_evaluate_raw_collection_rejects_unregistered_target`; `test_load_channel_policies_returns_none_for_unregistered_target` | PASS |
| P3-REQ-003 | Automated collection blocked unless `automation_authorization=GRANTED` | `evaluate_raw_collection` + `filter_authorized_targets` wired into `_run()` | parametrized unit test over all authorization values; live verification both channels remain `GRANTED` | PASS |
| P3-REQ-004 | Gate Decision (`PAUSED`/`REJECTED`) blocks collection; `MONITOR_ONLY`/`ENABLED` do not | same as above | parametrized unit test over all gate decisions | PASS |
| P3-REQ-005 (BR-001) | Channel type (`ANALYSIS`/`EXECUTION_SIGNAL`) never mixed for one channel | `channel_type` is a single `NOT NULL` `CHECK`-constrained column; one row per `(channel_id, topic_id)` | `test_channel_policy_rejects_invalid_channel_type`; structural (schema-level) guarantee | PASS |
| P3-REQ-006 | Raw Message uniqueness by channel/message/version; edit adds a version, never overwrites | unchanged from Phase 2 (`uq_telegram_message_version`), unaffected by topic_id addition | `test_telegram_replay_and_edit_versions_are_append_only` (pre-existing, still passing) | PASS |
| P3-REQ-007 | Forum-topic scoping: one Source Channel entry may be a specific topic within a group, not just a whole channel | `TelegramChannelTarget.topic_id`, migration 0004, `reply_to`-scoped backfill/filter | live verification: bonnie-blockchain topic 21 collects independently of other topics in the same group | PASS |
| P3-REQ-008 | Symbol scope mode declared per channel (`STATIC_ALLOWLIST` / `BINANCE_USDM_ACTIVE_PERPETUAL`) | `channel_policies.symbol_scope_mode` + `allowed_symbols`/`prohibited_symbols` | seeded and queryable; **not yet enforced by any parser** (no parser exists) | PARTIAL — data captured, enforcement is Phase 4 scope |
| P3-REQ-009 (FR-008) | AI processing / media-storage authorization blocks corresponding processing path | `ai_authorization`/`media_authorization` columns exist and are seeded `GRANTED` | **N/A — no AI processing or media-storage-consuming code exists yet to check them against** (Phase 5 scope) | N/A, not PASS |
| P3-REQ-010 | Retention policy declared per channel | `raw_retention_days` column, both seeded at 7 days | column exists and is queryable | PARTIAL — declared only, no deletion job exists |
| P3-REQ-011 | 20 representative fixtures per channel before Gate 3 | bonnie-blockchain: 20 real fixtures in [邦妮區塊鏈.md](../phase-0/channels/邦妮區塊鏈.md) | `@followgerry`: only 1 fixture in [monster-currency-universe.md](../phase-0/channels/monster-currency-universe.md) | **FAIL for `@followgerry`**, PASS for bonnie-blockchain |
| P3-REQ-012 | Markdown onboarding record and enforced table must not silently diverge | banner added to all 3 onboarding docs declaring the table authoritative | manual review; values cross-checked at migration authoring time | PASS |

## Acceptance Criteria

| AC | Given / When / Then | Evidence | Status |
|---|---|---|---|
| P3-AC-001 | Given a channel with no `channel_policies` row, when the collector starts, then that target is excluded and logged, not silently collected | unit test; code path unexercised live (no unregistered target was actually attempted live) | PASS (unit), PARTIAL (live) |
| P3-AC-002 | Given all configured targets are unauthorized, when the collector starts, then startup fails with `ChannelPolicyError` | `resolve_effective_targets()` (extracted specifically so this path is directly testable) | `test_resolve_effective_targets_fails_closed_when_none_authorized` | PASS |
| P3-AC-003 | Given a channel with `GRANTED`/`MONITOR_ONLY`, when the collector starts, then it collects normally, unchanged from pre-Phase-3 behavior | live verification, both channels | PASS |
| P3-AC-004 | Given a forum-topic target, when backfill runs, then only that topic's messages are persisted, tagged with the correct `topic_id` | live verification: 500 rows all `topic_id=21`, 0 cross-contamination from other topics in the same group | PASS |
| P3-AC-005 | Given the onboarding markdown and the `channel_policies` table, when they are compared, then they agree | manual cross-check at authoring time; no automated diff check exists | PASS (manual only) |

