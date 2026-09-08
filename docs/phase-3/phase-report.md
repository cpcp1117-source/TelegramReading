# Phase 3 Report

- **Phase:** Phase 3 — Channel Registry / Normalization
- **Version:** v0.1 (interim, pre-Gate)
- **Date:** 2026-09-08
- **Implementation Fixed Point:** `eea4f29`
- **Branch:** `phase/2-telegram-readonly-collector` (unchanged from Phase 2; no separate Phase 3 branch was created)
- **Gate Verdict:** `NOT READY` (see [gate-3-checklist.md](gate-3-checklist.md))
- **User Acceptance:** `PENDING`
- **Next Phase Permission:** `NOT GRANTED`

## 1. Outcome

Phase 3 delivered two things: (a) the raw collector now supports multiple Telegram sources including forum topics within a group, not just one whole channel, and (b) a `channel_policies` database table that turns each source's onboarding decisions (authorization scopes, Gate Decision) into something the collector actually reads and enforces at startup, instead of that information living only in markdown. Two real Telegram sources — `@followgerry` (whole channel) and the "邦妮區塊鏈-BTC ETH 即時更新" forum topic — are onboarded end-to-end and confirmed live-collecting in production with the policy gate active.

What Phase 3 did **not** deliver: the actual normalization/parsing layer (Raw Message → deterministic normalized output, symbol alias resolution, `NormalizedSignal`) remains Phase 4 scope, unstarted. This phase built the *registry and gate*, not the *normalizer*.

## 2. Completed Deliverables

| Deliverable | Status | Evidence |
|---|---|---|
| Multi-channel / forum-topic collector | Complete | `config.py` `TelegramChannelTarget` list; `telegram_collector.py` per-target resolve/backfill/filter; migration 0004 |
| `channel_policies` table + seed | Complete | migration 0005; both real channels' current declared values seeded |
| Channel Policy enforcement at collector startup | Complete | `channel_policy.py`; wired into `telegram_collector.py::_run()`; per-target skip + loud log, hard fail only if zero targets remain |
| Read-only onboarding CLI (`discover-private`, `preview`) | Complete | `telegram_cli.py`; never touches the DB or downloads media |
| `@followgerry` onboarding | Complete | identity/authorization/market-policy recorded; 21 fixtures (1 synthetic + 20 real, added 2026-09-08 via `preview`) — diversity caveats noted in the doc |
| Bonnie-blockchain BTC/ETH topic onboarding | Complete | [邦妮區塊鏈.md](../phase-0/channels/邦妮區塊鏈.md): A–E all filled, 20 real fixtures, Gate Decision `MONITOR_ONLY` |
| Live production verification | Complete | both channels collecting via `docker compose --profile telegram run`, migration 0005 applied to the real database, 0 message loss/duplication observed |
| Retention enforcement | Complete | migration 0006 (age-gated trigger bypass) + `scripts/retention_cleanup.py`; verified on a disposable Postgres, not yet applied to the real database |
| Live policy reload (shrink-only) | Complete | collector polls `channel_policies` every 5 min (default) and excludes a paused/revoked target without restart |

## 3. Explicitly Not Implemented

- Raw Message → Normalized deterministic transformation (text/caption normalization, symbol alias resolution) — Phase 4.
- `NormalizedSignal` / `TradeIntent` semantics, Control Bot, Binance API, AI provider integration — Phase 4/5/6, unchanged prohibition.
- AI-processing and media-storage authorization enforcement — captured as columns but nothing currently consumes them (no Phase 5 code exists yet to check them against); not something Phase 3 can close on its own.
- Stopping Telegram API/media-download traffic for a revoked channel mid-session — live reload stops the DB write only; Telethon's own chat filter is intentionally untouched (see [known-issues.md](known-issues.md) P3-MINOR-003).

## 4. Environment

| Item | Version / Configuration |
|---|---|
| Branch | `phase/2-telegram-readonly-collector` |
| Fixed point | `eea4f29` |
| Migration head | `0006_retention_cleanup` (verified on disposable Postgres; real database still on `0005` pending the next `collect` run) |
| Configured targets | `@followgerry` (`2439599598`, whole channel); bonnie-blockchain (`2382278102`, topic `21`) |
| PostgreSQL | 16.6-alpine, real instance (`telegram-trader-phase1-db-1`) + disposable throwaway containers for migration/integration verification |

## 5. Test Summary

- Unit suite: 86 passed, 0 failed.
- Integration suite (`-m integration`, disposable Postgres): 31 passed, 0 failed.
- Migration `0001`→`0006`: `upgrade head` → `downgrade -1` → `upgrade head` → `downgrade base` → `upgrade head`, all clean, on a disposable container.
- mypy: 0 issues (33 source files). Ruff check/format: clean. Secret scan: 94 files, 0 findings.
- Live production run: both channels collecting post-migration-0005, checkpoints intact, 0 duplicate `(channel_id, message_id, edit_version)` rows, `channel_policies` seed rows confirmed correct in the real database. Migration 0006 and the retention/live-reload code have **not** yet been run against the real database — verified on disposable Postgres only.

Full detail in [test-evidence.md](test-evidence.md).

## 6. Open Items

- Product Critical: 0.
- Product Major: 1 (see [known-issues.md](known-issues.md)) — AI/media authorization has nothing to enforce against until Phase 5 exists. (The other 3 — fixture shortfall, retention, live reload — were closed 2026-09-08.)
- Phase 2's 5 inherited risk-accepted gaps remain open and now apply across both channels, not just one.
- User acceptance has not been recorded.

## 7. Gate Decision

`NOT READY`. Per [phase-kickoff.md](phase-kickoff.md), Phase 3's own dependency note ("Gate 3 前擴充至 20 fixtures") is now satisfied for both onboarded channels. The registry/enforcement mechanism, retention, and live policy reload are all built, tested, and verified (retention/live-reload on disposable Postgres; not yet run against the real database). The one remaining Major item (AI/media authorization enforcement) cannot actually be closed by Phase 3 — there's no Phase 5 code yet to enforce against. See [gate-3-checklist.md](gate-3-checklist.md) for the itemized checklist and the two honest options for how to record that.
