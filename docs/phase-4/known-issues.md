# Phase 4 Known Issues

## Open Product Findings

| Severity | Count | Items |
|---|---:|---|
| Critical | 0 | None |
| Major | 1 | P4-MAJOR-001 (edited-message/supersede linking not implemented) |
| Minor | 4 | See below |

Three real Major-severity bugs were found during live testing this phase; all three are closed (see Resolved). The one open Major item is a genuine scope gap discovered while writing this Gate package, not a defect in code that was believed complete.

## Major

| ID | Item | Detail |
|---|---|---|
| P4-MAJOR-001 | Edited-message and "supersede" lifecycle linking is not implemented | `parse_signals.py`'s follow-up linking (`_find_parent`) matches only on `reply_to_message_id` (a reply with a cancel/close keyword, or a reply carrying only new SL/TP values). Editing a Telegram message does **not** change `reply_to_message_id` — Telegram delivers an edit as an update to the same message, and the collector persists it as a new `telegram_message_versions` row for the same `raw_message_id` lineage. Because the parser's linking logic never looks at edit history, an edited message is evaluated as if it were a brand-new, unrelated message: if the edited text still looks like a signal attempt, it produces an entirely independent new `signal_id`/aggregate (duplicating, not superseding, the original); if the edited text no longer looks like a signal attempt, it silently produces no row at all, leaving the original signal's `NEW`/`VALIDATED` status unchanged even though the author retracted or altered it. This is a direct gap against the Gate 4 test-strategy's explicit "Negation, cancel, edit, supersede and expiry" requirement — confirmed by source inspection (`grep -n "reply_to_message_id\|event_kind\|EDITED" src/telegram_trader/parse_signals.py`, 4 matches, none implementing edit-based linking) while drafting this Gate package. **Risk-accepted 2026-09-12** (see [gate-4-checklist.md](gate-4-checklist.md) User Acceptance Record) rather than blocking Phase 5: a check against `telegram_message_versions` confirmed neither onboarded channel has ever actually produced an edited message, so this remains a real but currently-unobserved gap, carried forward as a mandatory item to close before Production Gate 8 (or sooner, if either channel is ever seen to produce an edit). |

## Resolved

| ID | Item | Resolution |
|---|---|---|
| P4-MAJOR-002 | Bonnie-blockchain's bare asset names (`BTC`, `ETH`) all resolved `INVALID` under `STATIC_ALLOWLIST` | Closed 2026-09-10: `resolve_symbol` did exact-string matching against the `BTCUSDT`/`ETHUSDT` allowlist, but authors write the bare asset name without a quote currency. Fixed by trying common quote-currency suffixes (`USDT`/`USD`/`BUSD`) before giving up (`_candidate_aliases`), under a bumped `CURRENT_NORMALIZER_VERSION` (`v1`→`v2`) so the old `v1` rows are left untouched and a fresh `v2` pass reprocesses the whole backlog. Verified against real data: 475/482 candidates now `VALID` (was near-zero for this channel before the fix). `BINANCE_USDM_ACTIVE_PERPETUAL` scope was deliberately left untouched by this fix (see [test-evidence.md](test-evidence.md) §6). |
| P4-MAJOR-003 | Control Bot crash-looped on startup: `ValueError: Too many bytes for the data` | Closed 2026-09-11: found on the Control Bot's first live run (user pasted the container's crash-loop logs). Root cause: inline-button callback data encoded the full 64-character `request_id` plus a 32-character `nonce` (~112 bytes); Telegram's Bot API caps callback data at 64 bytes, so every `Button.inline()` call raised inside the notification poll loop, which retried forever. Fixed by resolving a button tap back to its request by `nonce` alone (`find_request_by_nonce`), after adding a `UNIQUE` constraint on `signal_decision_requests.nonce` (migration `0010_decision_nonce_unique`, additive since `0009_signal_decisions` was already applied). New callback payloads are ~47 bytes; a regression test now asserts every encoded payload stays `<= 64` bytes. The migration's own first revision id (`0010_signal_decision_nonce_unique`, 33 characters) separately failed to apply (`StringDataRightTruncation`, Alembic's default `version_num` column is `VARCHAR(32)`) — caught during disposable-Postgres re-verification before the fix was called done, and fixed by shortening the revision id to `0010_decision_nonce_unique` (26 characters). |
| P4-MAJOR-004 | Control Bot crashed on every button tap: `AttributeError: 'UpdateBotCallbackQuery' object has no attribute 'id'` | Closed 2026-09-11: found on the Control Bot's second live run, immediately after P4-MAJOR-003's fix let a notification actually get tapped (user pasted the traceback). Root cause: `_on_callback` read `event.query.id`, but Telethon's `CallbackQuery.Event.id` is the correct accessor (a convenience property); `event.query` is the raw `UpdateBotCallbackQuery`, whose own field is `query_id`, not `id`. The existing unit test's `FakeEvent` double had modeled the same wrong `.query.id` shape, so it could not have caught this. Fixed by changing to `event.id` and correcting `FakeEvent` to expose `id` directly, matching real Telethon. Live-verified afterward: a real signal (`ZEC`) was approved end-to-end via button tap with no error. |

## Minor

| ID | Item | Note |
|---|---|---|
| P4-MINOR-001 | Cancel/close-keyword linking, wrong-side-SL rejection, and `LIMIT`/`RANGE` entry parsing are unit/integration-tested only against synthetic fixtures | No real message of these shapes has occurred in either onboarded channel's backlog yet; behavior is verified logically, not against a real-world example |
| P4-MINOR-002 | No live 24-hour soak of the Control Bot's own Telegram connection resilience | Reconnect-after-drop is unit-tested against `FakeBotClient` only, mirroring the same gap already open for the collector since Phase 2 |
| P4-MINOR-003 | `close_all`'s double-confirmation window is held in the bot process's memory, not persisted | A Control Bot restart between the `/close_all` command and its confirmation tap silently drops the pending confirmation (the user must re-issue `/close_all`); acceptable since there is no execution capability yet for `close_all` to actually guard |
| P4-MINOR-004 | Automatic `EXPIRED`/`SUPERSEDED` transitions on `normalized_signals.status` are not implemented | Only the separate Control Bot decision-request's own `expires_at` is enforced (and was live-verified); a stale signal's row in `normalized_signals` itself still shows `NEW`/`VALIDATED` forever. Reserved for a future consumer per [logical-data-model.md](../phase-0/logical-data-model.md) §4's state machine — Phase 4 never claimed to own this transition |

## Inherited From Phase 3 (still open)

Per [../phase-3/gate-3-checklist.md](../phase-3/gate-3-checklist.md), P3-MAJOR-004 (AI-processing / media-storage authorization enforcement) remains structurally deferred, converted into a mandatory Phase 5 Definition-of-Done item. Untouched by Phase 4 — no AI or media-processing code was added this phase.

## Inherited From Phase 2 (still open)

Per [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md): 24-hour continuous read-only run, controlled-restart no-loss proof, live edited-message version retention, full manual reconciliation beyond a sample, and combined unit+integration coverage re-verification at ≥85%. Now apply across the normalization/parsing/Control Bot pipeline as well as the raw collector, since all of it sits downstream of the same collector process.

## Non-blocking Notices

| ID | Notice | Gate Impact |
|---|---|---|
| NOTICE-001 | The `runtime` Docker build stage never copied `scripts/`, inherited unnoticed from Phase 3 since Phase 3 never ran a `scripts/` entrypoint inside the built runtime image the way Phase 4's `normalize_content.py`/`parse_signals.py`/control-bot bootstrap commands do. Fixed by adding `COPY scripts ./scripts` to the `runtime` stage; verified by running the built image's scripts directly. | None |
| NOTICE-002 | `telegram-bootstrap.ps1` re-prompted for `POSTGRES_PASSWORD` interactively on every `collect` run instead of reading it from `.env` like every other credential, risking a mismatch between the value typed and the value the database was actually initialized with. Fixed to read-from-`.env`-first, matching the existing pattern for all other secrets. Found and fixed before it caused a real incident beyond one confusing failed connection. | None |
