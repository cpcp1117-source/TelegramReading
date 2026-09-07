# Phase 3 Report

- **Phase:** Phase 3 — Channel Registry / Normalization
- **Version:** v0.1 (interim, pre-Gate)
- **Date:** 2026-09-08
- **Implementation Fixed Point:** `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813`
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
| `@followgerry` onboarding | Partial | identity/authorization/market-policy recorded; **only 1 of 20 required fixtures exists** |
| Bonnie-blockchain BTC/ETH topic onboarding | Complete | [邦妮區塊鏈.md](../phase-0/channels/邦妮區塊鏈.md): A–E all filled, 20 real fixtures, Gate Decision `MONITOR_ONLY` |
| Live production verification | Complete | both channels collecting via `docker compose --profile telegram run`, migration 0005 applied to the real database, 0 message loss/duplication observed |

## 3. Explicitly Not Implemented

- Raw Message → Normalized deterministic transformation (text/caption normalization, symbol alias resolution) — Phase 4.
- `NormalizedSignal` / `TradeIntent` semantics, Control Bot, Binance API, AI provider integration — Phase 4/5/6, unchanged prohibition.
- Automated retention/deletion enforcement — `channel_policies.raw_retention_days` is recorded (7 days for both channels) but nothing in the codebase deletes aged data yet.
- Live/dynamic policy reload — a revoked or paused Channel Policy only takes effect on the collector's next restart, not while a session is connected.
- AI-processing and media-storage authorization enforcement — captured as columns but nothing currently consumes them (no Phase 5 code exists yet to check them against).

## 4. Environment

| Item | Version / Configuration |
|---|---|
| Branch | `phase/2-telegram-readonly-collector` |
| Fixed point | `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813` |
| Migration head | `0005_channel_policies` |
| Configured targets | `@followgerry` (`2439599598`, whole channel); bonnie-blockchain (`2382278102`, topic `21`) |
| PostgreSQL | 16.6-alpine, real instance (`telegram-trader-phase1-db-1`) + disposable throwaway containers for migration/integration verification |

## 5. Test Summary

- Unit suite: 79 passed, 0 failed.
- Integration suite (`-m integration`, disposable Postgres): 22 passed, 0 failed.
- Migration `0001`→`0005`: `upgrade head` → `downgrade -1` → `upgrade head` → `downgrade base` → `upgrade head`, all clean, on a disposable container.
- mypy: 0 issues (31 source files). Ruff check/format: clean. Secret scan: 85 files, 0 findings.
- Live production run: both channels collecting post-migration-0005, checkpoints intact, 0 duplicate `(channel_id, message_id, edit_version)` rows, `channel_policies` seed rows confirmed correct in the real database.

Full detail in [test-evidence.md](test-evidence.md).

## 6. Open Items

- Product Critical: 0.
- Product Major: 4 (see [known-issues.md](known-issues.md)) — `@followgerry` fixture shortfall, no retention enforcement, no live policy reload, AI/media authorization not yet enforced anywhere.
- Phase 2's 5 inherited risk-accepted gaps remain open and now apply across both channels, not just one.
- User acceptance has not been recorded.

## 7. Gate Decision

`NOT READY`. Per [phase-kickoff.md](phase-kickoff.md), Phase 3's own dependency note ("Gate 3 前擴充至 20 fixtures") is only satisfied for one of the two onboarded channels. The registry/enforcement mechanism itself is built, tested, and live-verified — the gap is in onboarding completeness (`@followgerry` fixtures) and in features intentionally deferred to later phases (retention enforcement, live reload). See [gate-3-checklist.md](gate-3-checklist.md) for the itemized checklist and recommended path forward.
