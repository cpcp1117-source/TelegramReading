# Phase 4 Test Evidence

- **Fixed Point:** `3a89cf4`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Credentials:** no credential value appears below; PostgreSQL, Telegram user, and Control Bot credentials were entered only at the local terminal / local `.env`

## 1. Static Checks

| Command | Result |
|---|---|
| `uv run ruff check .` | PASS; all checks passed |
| `uv run ruff format --check .` | PASS |
| `uv run mypy` | PASS; 44 source files, 0 issues |
| `uv run python scripts/secret_scan.py --root .` | PASS; 110 files, 0 findings |

## 2. Unit Suite (non-integration)

| Command | Result |
|---|---|
| `uv run pytest -m "not integration"` | PASS; 160 passed, 62 deselected |

Coverage includes: `normalize_text`/`extract_symbol_candidates`/`resolve_symbol` determinism and the `BINANCE_USDM_ACTIVE_PERPETUAL → PENDING_MARKET_DATA` rule (`test_normalization.py`); the signal parser's field extraction grounded in real fixture categories from monster-currency-universe.md (`小多`/`風險短多` modifiers, promotional-text non-attempts, the explicit WAIT marker, market entry with a quoted reference price, author SL+TP with a still-`MARKET` entry, leverage-without-side-text, negated direction) plus synthetic-only cases for cancel, wrong-side SL, and LIMIT/RANGE entries (`test_signal_parser.py`); `determine_status`'s `PENDING_MARKET_DATA → NEW` rule; Control Bot callback encode/decode round-trips including an explicit assertion that encoded payloads stay at or under Telegram's 64-byte callback-data limit; allowlist rejection is silent for both commands and callbacks (NFR-007); `close_all` double-confirmation state machine (valid window, expired window, wrong nonce); stub commands (`/pause`/`/resume`/`/close`) reply with the "no execution component" message; unknown commands from the allowlisted user get a help reply; the background poll task retries after an unexpected error and cancels cleanly on shutdown (`test_control_bot.py`).

## 3. Integration Suite (disposable Postgres, `-m integration`)

| Command | Result |
|---|---|
| `uv run pytest -m integration` (against a throwaway container, correct `APP_DATABASE_*`/`TEST_DATABASE_URL`) | PASS; 62 passed, 160 deselected |

New coverage this phase: `run_normalization`'s alias resolution fix (bare asset via quote-currency alias); `run_signal_parsing` reaching `VALIDATED` for a `STATIC_ALLOWLIST` fixture, staying at `NEW` for `BINANCE_USDM_ACTIVE_PERPETUAL`, `INCOMPLETE` for missing side, and producing no row for promotional text; a reply-cancel producing a linked `CANCELLED` revision; a reply-with-new-SL producing a linked revision with the stop updated and status carried forward; idempotency and version-bump-appends-new-row for both the normalizer and the signal parser; `normalized_signals`' append-only trigger and cascade delete via retention cleanup; the `channel_type` gate excluding an `ANALYSIS` channel entirely; `load_pending_signals`/`create_request`/`record_decision` happy paths (`APPROVED`/`REJECTED`); `REJECTED_STALE` for both an already-decided request and a request whose signal has since gained a newer revision (a reply-cancel arrived after the request was created); `REJECTED_EXPIRED` past `expires_at`; idempotent redelivery of the same Telegram `callback_query_id` (the bug found and fixed this phase -- see §6); `signal_decision_requests`/`signal_decision_events`' append-only triggers and cascade delete down through both new tables via retention cleanup.

## 4. Migration Verification (disposable Postgres, not the user's real database)

| Step | Result |
|---|---|
| `alembic upgrade head` from empty (`0001`→`0010`) | PASS |
| `alembic downgrade -1` (`0010`→`0009`); confirms the added `UNIQUE` constraint is dropped | PASS |
| `alembic upgrade head` (`0009`→`0010`); confirms it's reapplied | PASS |
| `alembic downgrade base` (full revert to empty) | PASS |
| `alembic upgrade head` (full replay `0001`→`0010`) | PASS |

Re-run in full after each of the three real bugs found this phase (see §6), each time on a freshly recreated disposable container, before any fix was considered done.

## 5. Live Production Verification

Applied to the real database (`telegram-trader-phase1-db-1`) and run against the real Telegram channels via `.\scripts\telegram-bootstrap.ps1 collect` / `control-bot`, over several sessions as fixes landed:

| Check | Result |
|---|---|
| `alembic_version` after run | `0010_decision_nonce_unique` |
| `normalize_content.py` against real backlog | 1016 messages normalized under `v2`; bonnie-blockchain's `BTC`/`ETH` correctly resolve `VALID` after the alias fix (475 `VALID`, 7 `INVALID`) |
| `parse_signals.py` against real backlog | `@followgerry`: 172 signals produced (92 `INCOMPLETE`, 80 `NEW`, 0 `VALIDATED` -- correct, dynamic scope), 357 messages correctly skipped as non-attempts; bonnie-blockchain: 0 signal rows (channel_type gate) |
| Control Bot notification | real `NEW`-status signals (e.g. `ASTER`, `COTI`, `ZEC`) received as Telegram messages with Approve/Reject buttons |
| Control Bot approval | a fresh signal (`ZEC`) approved via button tap; bot replied "已核准（僅記錄，不會自動下單）"; no order was placed (no execution component exists) |
| Control Bot expiry enforcement | a backlogged signal (`COTI`, notified only after Control Bot went live, but originating from a message already past its 24h `expires_at`) was correctly rejected as expired when tapped -- the intended fail-closed behavior, not a bug |
| Control Bot allowlist enforcement | a message from a non-allowlisted sender (`8829571367`) was silently rejected and logged, confirming NFR-007's rejection path live, not just in unit tests |
| Process shutdown | user-initiated stop, expected clean exit, not a crash |

No `close_all` double-confirmation flow, no cancel/reply-based follow-up, and no wrong-side-SL case were exercised live -- those remain verified on disposable Postgres / synthetic fixtures only (see §6 and [known-issues.md](known-issues.md)).

## 6. Real Bugs Found and Fixed This Phase (via live testing, before being declared stable)

| # | Bug | Root Cause | Fix | Evidence It's Fixed |
|---|---|---|---|---|
| 1 | Bonnie-blockchain's `BTC`/`ETH` all resolved `INVALID` | `resolve_symbol` did exact-string matching against the `BTCUSDT`/`ETHUSDT` allowlist; authors write the bare asset name | Try common quote-currency suffixes (`USDT`/`USD`/`BUSD`) before giving up; `BINANCE_USDM_ACTIVE_PERPETUAL` untouched | Re-ran `normalize_content.py` under a bumped `v2`; 475/482 now `VALID` |
| 2 | `signal_id`+`revision` collided across a `parser_version` bump | Uniqueness constraint didn't include `parser_version`, but `signal_id` is deliberately stable across a version bump | Constraint changed to `(signal_id, revision, parser_version)`; `_next_revision` scoped per version | Integration test `test_run_signal_parsing_version_bump_appends_new_row` |
| 3 | Control Bot crash-looped: `ValueError: Too many bytes for the data` | Callback data embedded the full 64-char `request_id` + 32-char `nonce` (~112 bytes); Telegram caps callback data at 64 bytes | Resolve a button tap by `nonce` alone (now `UNIQUE`-constrained, migration 0010); callback data is now ~47 bytes | Regression test asserts every encoded payload is `<= 64` bytes; live-verified, notification+approval now work |
| 4 | Migration 0010 itself failed to apply: `value too long for type character varying(32)` | Its own revision id (`0010_signal_decision_nonce_unique`, 33 chars) exceeded Alembic's default 32-character `version_num` column | Shortened to `0010_decision_nonce_unique` (26 chars) | Full disposable-Postgres round-trip re-verified before reapplying to the real database |
| 5 | Control Bot crashed on every button tap: `AttributeError: 'UpdateBotCallbackQuery' object has no attribute 'id'` | Used `event.query.id`; Telethon's `CallbackQuery.Event.id` is the correct accessor, `event.query` is the raw update whose own field is `query_id` | Changed to `event.id`; the test double (`FakeEvent`) was also wrong in the same way and has been corrected to mirror Telethon's real shape | Live-verified: Approve/Reject now work end-to-end |

Bugs 1-2 were found via disposable-Postgres testing and real-data spot-checks before touching the real database. Bugs 3-5 were only found by the user actually running the Control Bot live -- none of the pre-existing unit or integration tests exercised Telegram's real 64-byte callback limit or Telethon's actual `CallbackQuery.Event` attribute shape, because the test doubles encoded the same incorrect assumptions as the production code. Regression tests were added for bugs 2, 3, and 5; bug 4 is now guarded by always completing a full disposable-Postgres round-trip (including the exact revision id) before applying any new migration to the real database.

## 7. Known Gaps Not Covered By This Evidence

- **Edited-message and "supersede" linking is not implemented at all** (see [known-issues.md](known-issues.md) P4-MAJOR-001) -- no test exists for it because the capability doesn't exist yet, not because testing was skipped.
- Cancel/close-keyword linking, wrong-side-SL rejection, and `LIMIT`/`RANGE` entry parsing are verified only against synthetic fixtures; no real message of these shapes has occurred in either channel yet.
- No live exercise of the `close_all` double-confirmation flow, or of a reply-based SL/TP update, against the real running Control Bot.
- No 24-hour soak of the Control Bot's own connection resilience (reconnect-after-drop is unit-tested with a fake client only).
- Phase 2's 5 inherited gaps (24h soak, restart no-loss, live edit-version retention, full reconciliation, combined coverage) remain unaddressed -- see [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md).
- Phase 3's P3-MAJOR-004 (AI/media authorization enforcement) remains structurally deferred to Phase 5, untouched by Phase 4.
