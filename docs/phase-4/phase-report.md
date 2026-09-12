# Phase 4 Report

- **Phase:** Phase 4 — Signal Lifecycle / Control Bot
- **Version:** v0.1 (interim, pre-Gate)
- **Date:** 2026-09-11
- **Implementation Fixed Point:** `3a89cf4`
- **Branch:** `phase/2-telegram-readonly-collector` (unchanged from Phase 2/3; no separate Phase 4 branch was created)
- **Gate Verdict:** `READY` (risk-accepted; see [gate-4-checklist.md](gate-4-checklist.md))
- **User Acceptance:** `ACCEPTED` (2026-09-12)
- **Next Phase Permission:** `GRANTED`

## 1. Outcome

Phase 4 delivered the three slices scoped in [phase-kickoff.md](phase-kickoff.md): (1) a deterministic normalization layer turning raw Telegram messages into `normalized_content` with symbol resolution, (2) an `EXECUTION_SIGNAL` parser turning normalized content into a `normalized_signal` with an explicit lifecycle, and (3) a private, allowlisted Control Bot that notifies the user about pending signals and records their approve/reject decisions. All three slices are verified against real production data for both onboarded channels, and the Control Bot has been live-tested end-to-end by the user, including two real bugs found and fixed only by that live run.

What Phase 4 did **not** deliver, matching its own scope decisions made with the user before implementation: no `trade_intent` record (deferred to Phase 6, when the Risk Engine exists to consume it), no Binance API access of any kind (not even public market data — that starts Phase 5), no AI/ANALYSIS content extraction (Phase 5), and no real execution capability behind the Control Bot's `pause`/`resume`/`close`/`close_all` commands (they reply honestly that no Execution Gateway exists yet).

## 2. Completed Deliverables

| Deliverable | Status | Evidence |
|---|---|---|
| Normalization layer (`normalized_content`) | Complete | migration 0007; `normalization.py`/`normalize_content.py`; 1016 real messages normalized under `v2` |
| Symbol alias resolution (bare asset → quoted pair) | Complete | fixed after real-data spot-check found bonnie-blockchain's `BTC`/`ETH` all `INVALID`; corrected to 475 `VALID` / 7 `INVALID` |
| `EXECUTION_SIGNAL` parser + signal lifecycle (`normalized_signals`) | Complete | migration 0008; `signal_parser.py`/`parse_signals.py`; 172 real signals parsed for `@followgerry` (92 `INCOMPLETE`, 80 `NEW`, 0 `VALIDATED` -- correct, dynamic-scope channel) |
| `channel_type` gate (only `EXECUTION_SIGNAL` channels parsed) | Complete | bonnie-blockchain (`ANALYSIS`) produced 0 signal rows, confirmed live |
| Control Bot (`signal_decision_requests`/`signal_decision_events`) | Complete | migrations 0009-0010; `control_bot.py`/`signal_decisions.py`; live-verified: notification sent, an expired decision correctly rejected, a fresh decision correctly approved, an unauthorized sender correctly and silently rejected |
| Dockerfile/`scripts/` runtime image gap (inherited from Phase 3) | Fixed | `scripts/` was never copied into the `runtime` build target; fixed, verified inside the built image |
| `telegram-bootstrap.ps1` password re-entry gap | Fixed | `POSTGRES_PASSWORD` now read from `.env` like other credentials, instead of re-prompted (and risking a mismatch) on every `collect` run |

## 3. Explicitly Not Implemented

- `TradeIntent` creation/submission -- Phase 6 (Risk Engine is the only consumer).
- Binance API access of any kind, including public market data -- Phase 5.
- AI/ANALYSIS content extraction, Strategy Contract, Candidate Trade -- Phase 5.
- Real execution behind `pause`/`resume`/`close`/`close_all` -- Phase 6/8; these commands are implemented and allowlist-checked, but honestly explain that no Execution Gateway exists yet, per the user's explicit scope choice.
- **Edited-message and "supersede" lifecycle linking** -- an edited Telegram message is not linked back to the signal it originated; the parser only links via `reply_to_message_id`, and an edit does not change that field. An edited message is currently processed as an entirely independent new signal aggregate (or silently produces no update at all if the edited text no longer looks like a signal attempt). See [known-issues.md](known-issues.md) P4-MAJOR-001.
- Automatic `EXPIRED`/`SUPERSEDED` transitions on `normalized_signals.status` itself -- only the Control Bot's own decision-request expiry is enforced (and was live-verified); a stale signal's row still shows `NEW`/`VALIDATED` forever in the table itself.

## 4. Environment

| Item | Version / Configuration |
|---|---|
| Branch | `phase/2-telegram-readonly-collector` |
| Fixed point | `3a89cf4` |
| Migration head | `0010_decision_nonce_unique` (applied to the real database) |
| Configured targets | `@followgerry` (`2439599598`, `EXECUTION_SIGNAL`, `BINANCE_USDM_ACTIVE_PERPETUAL`); bonnie-blockchain (`2382278102`, topic `21`, `ANALYSIS`, `STATIC_ALLOWLIST`) |
| PostgreSQL | 16.6-alpine, real instance (`telegram-trader-phase1-db-1`) + disposable throwaway containers for all migration/integration verification |
| New credential this phase | `CONTROL_BOT_TOKEN` (Telegram Bot API, separate from the collector's MTProto user session), `CONTROL_BOT_ALLOWLISTED_USER_ID` |

## 5. Test Summary

- Unit suite: 160 passed, 0 failed.
- Integration suite (`-m integration`, disposable Postgres): 62 passed, 0 failed.
- Migration `0001`→`0010`: `upgrade head` → `downgrade -1` → `upgrade head` → `downgrade base` → `upgrade head`, all clean, on disposable containers, re-verified after every fix.
- mypy: 0 issues (44 source files). Ruff check/format: clean. Secret scan: 110 files, 0 findings.
- Live production verification: both channels' backlog processed end-to-end (normalize → parse → notify → decide); real bugs found and fixed via this live run (see [known-issues.md](known-issues.md) Resolved section) before being declared stable.

Full detail in [test-evidence.md](test-evidence.md).

## 6. Open Items

- Product Critical: 0.
- Product Major: 1 -- edited-message/supersede lifecycle linking is not implemented (see [known-issues.md](known-issues.md) P4-MAJOR-001).
- Product Minor: several, including synthetic-only test coverage for cancel/wrong-side-SL/LIMIT-RANGE-entry paths, no live 24h soak of the Control Bot's own connection resilience, and the in-memory (non-persistent) `close_all` confirmation window.
- Phase 3's one structurally-deferred item (P3-MAJOR-004, AI/media authorization) remains open as a Phase 5 Definition-of-Done item, untouched by Phase 4.
- Phase 2's 5 inherited risk-accepted gaps remain open.
- User acceptance recorded 2026-09-12 (see below).

## 7. Gate Decision

`READY`, risk-accepted. The core pipeline works end-to-end and has been live-verified against real data, including three real bugs found and fixed only by that live testing (a genuinely useful outcome of doing it, not just simulation). The Gate 4 test-strategy explicitly names "edit" and "supersede" as required lifecycle scenarios, and honest investigation while writing this report found that edited messages are not actually linked to their originating signal at all -- a real, unaddressed gap, not a documentation oversight. A check against the real backlog (`telegram_message_versions` grouped by `raw_message_id`, looking for more than one version) confirmed neither onboarded channel has ever actually produced an edited message to date. Given that, the user explicitly chose to risk-accept this gap rather than block Phase 5 on it: "不太需要為了編輯訊息而卡住，如果其他的phase4的功能都正常就開始規劃phase5功能" (no need to get stuck on the edited-message issue -- if everything else in Phase 4 works normally, start planning Phase 5). P4-MAJOR-001 remains open and carried forward, not resolved. See [gate-4-checklist.md](gate-4-checklist.md) for the itemized checklist and full acceptance record.
