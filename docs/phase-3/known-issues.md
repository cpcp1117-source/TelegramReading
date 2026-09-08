# Phase 3 Known Issues

## Open Product Findings

| Severity | Count | Items |
|---|---:|---|
| Critical | 0 | None |
| Major | 1 | See below (3 additional items were closed 2026-09-08 — see Resolved section) |
| Minor | 2 | See below |

No defect was found in the `channel_policies` schema, enforcement logic, or the multi-topic collector during this phase's testing. The items below are onboarding/completeness gaps and intentionally-deferred scope, not bugs in what was built.

## Major

| ID | Item | Why it matters | Owner |
|---|---|---|---|
| P3-MAJOR-004 | AI-processing / media-storage authorization not enforced by any code | `ai_authorization`/`media_authorization` columns are captured and seeded `GRANTED` but nothing consumes them yet (no Phase 5 code exists) — flagging now so Phase 5 doesn't skip checking them when that code is written. Not something to build now — there's nothing to enforce against until Phase 5 exists. | Technical (Phase 5) |

## Resolved

| ID | Item | Resolution |
|---|---|---|
| P3-MAJOR-001 | `@followgerry` had only 1 of the required 20 fixtures | Closed 2026-09-08: 20 real messages pulled via `preview` and classified into [monster-currency-universe.md](../phase-0/channels/monster-currency-universe.md) §5 (21 fixtures total). The real sample surfaced a WAIT/no-trade-intent case, an ambiguous-side case, and revealed that roughly half this channel's traffic is promotional/no-signal content — all useful for Phase 4. Still missing from the real sample (flagged in that doc, not blocking): cancel signal, invalid/wrong-side SL, limit/range entry, reply-based updates, an edited message, a duplicate repost, a stale signal, and a pure-image-only signal. |
| P3-MAJOR-002 | No automated retention/deletion enforcement | Closed 2026-09-08: migration 0006 replaces the append-only trigger function so a `DELETE` is allowed only when a transaction-scoped `SET LOCAL app.retention_cleanup='on'` is set **and** the row is actually past its channel's declared `raw_retention_days` (checked server-side in the trigger) — an unconditional bypass was rejected in favor of this stronger, age-gated one. `scripts/retention_cleanup.py` runs the cleanup and deletes the matching media files after each batch commits. Not wired into the always-connected collector; it's a separate periodic maintenance entry point, same category as `scripts/secret_scan.py`. |
| P3-MAJOR-003 | No live/dynamic policy reload | Closed 2026-09-08: the collector now polls `channel_policies` every 5 minutes (default, configurable via `POLICY_POLL_INTERVAL_SECONDS`) and shrinks its active target set if a channel is paused/revoked, without a restart. Scoped to shrinking only — a newly-authorized target still needs a restart. **Residual gap, not solved**: this stops the DB write, not Telegram API/media-download traffic for a revoked channel, since Telethon's own chat filter is intentionally left untouched (see [test-evidence.md](test-evidence.md)). |

## Minor

| ID | Item | Note |
|---|---|---|
| P3-MINOR-001 | Only 2 channels total registered | The Channel Policy mechanism itself is not stress-tested at a scale beyond 2 — reasonable for now per the user's explicit choice to use only these two channels |
| P3-MINOR-002 | Symbol-scope enforcement (`STATIC_ALLOWLIST`/`BINANCE_USDM_ACTIVE_PERPETUAL`) is captured but not enforced by any parser | Expected — no parser exists until Phase 4 |
| P3-MINOR-003 | Live policy reload stops the DB write but not Telegram API/media-download traffic for a revoked channel | `telegram_message_input()` still calls `client.download_media()` before `persist()`'s target check runs; fixing this would require touching Telethon's `chats` filter dynamically, out of scope for the shrink-only design chosen |

## Inherited From Phase 2 (still open)

Per [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md), the following remain unresolved and now apply across both onboarded channels, not just one: 24-hour continuous read-only run, controlled-restart no-loss proof, live edited-message version retention, full manual reconciliation beyond a sample, and combined unit+integration coverage re-verification at ≥85%.

## Non-blocking Notices

| ID | Notice | Gate Impact |
|---|---|---|
| NOTICE-001 | The collector Docker image did not rebuild automatically before this phase's first `collect` run, so the container briefly ran a stale, pre-multi-topic image and migration 0004 didn't apply. Fixed by forcing a rebuild (`docker compose --profile telegram build collector`) before migration/collect in `telegram-bootstrap.ps1`. Root-caused and corrected within this phase; no lasting effect (verified via direct database query after the fix). | None |
