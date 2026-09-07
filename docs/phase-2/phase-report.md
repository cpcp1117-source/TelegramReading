# Phase 2 Report

- **Phase:** Phase 2 — Telegram Read-only Collector
- **Version:** v0.1 (interim, pre-Gate)
- **Date:** 2026-09-06
- **Implementation Fixed Point:** `f4ccff6a039f42df35fc81054c9208861b42ecd3`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Gate Verdict:** `NOT READY` (5 acceptance criteria PENDING/PARTIAL — see [gate-2-checklist.md](gate-2-checklist.md))
- **User Acceptance:** `RISK-ACCEPTED 2026-09-06` (explicit override; not a claim that all acceptance criteria passed)
- **Next Phase Permission:** `GRANTED WITH OPEN RISK 2026-09-06` (deviation from [ADR-0001](../adr/0001-sequential-stage-gates.md), disclosed to and accepted by the user)

## 1. Outcome

Phase 2 的 Telegram 唯讀 Collector 已實作完成核心功能路徑，並在真實 `@followgerry` 頻道上完成初步 live 驗證：登入、dialog listing 確認目標頻道、history backfill、checkpoint 續傳、controlled restart 後零重複、內容正確性抽樣核對均已通過。**24 小時 soak、即時編輯訊息驗證、完整 500 筆人工核對、Telegram session 安全檢查**已個別執行；其中 session 安全檢查已通過，其餘尚未完整完成，Gate 2 尚未進入評估。

## 2. Completed Deliverables

| Deliverable | Status | Evidence |
|---|---|---|
| Credential-safe login/bootstrap | Complete | `telegram-bootstrap.ps1`; phone/code/2FA entered only at local terminal |
| Channel dialog listing with allowlist | Complete | `dialogs` command; `target_found=true`, 2026-09-06 |
| Telethon read-only client (NewMessage/EditedMessage handlers) | Implemented | `telegram_collector.py`; fake-client unit tests pass |
| History backfill with overlap-based resume | Implemented, live-validated | live backfill 500 rows; restart re-scan 95 rows, 0 duplicates |
| Reply/forward relationship capture | Implemented | `telegram_message_input`; schema constraints |
| Text/caption extraction | Implemented, live-validated | 150 text + 209 caption rows, manually spot-checked correct |
| Image download and media hash | Implemented, live-validated | 108 image rows persisted via `MediaStore` |
| Dedup by `(channel_id, message_id, edit_version)` | Implemented, live-validated | unique constraint; 0 duplicate groups after restart |
| Collector checkpoint | Implemented, live-validated | checkpoint stable at `6655` across restart |
| Reconnect/retry with capped backoff | Implemented (unit-tested only) | `reconnect_delay`; not yet exercised live |
| Telegram session security | Verified | [security-check.md](security-check.md): PASS |

## 3. Explicitly Not Implemented / Not Yet Verified

- 24-hour continuous read-only soak (longest run so far ~21 minutes).
- Live edited-message capture (no edit occurred in the channel during the observed window).
- Full 500-row manual message reconciliation against `@followgerry` (20 spot-checked so far).
- Combined unit+integration coverage re-run for this fixed point.
- Trading-signal parsing, `NormalizedSignal`/`TradeIntent`, Binance API, OpenAI API, Control Bot — remain out of scope per [phase-kickoff.md](phase-kickoff.md), unchanged.

## 4. Environment

| Item | Version / Configuration |
|---|---|
| Branch | `phase/2-telegram-readonly-collector` |
| Fixed point | `f4ccff6a039f42df35fc81054c9208861b42ecd3` |
| Target channel | `@followgerry` / `channel_id=2439599598` |
| PostgreSQL | 16.6-alpine (`telegram-trader-phase1-db-1`) |
| Local OS | Windows 11 Pro |
| Docker | Docker Desktop (local) |

## 5. Test Summary

- Unit suite (non-integration): 50 passed, 0 failed.
- Repository secret scan: 73 files, 0 findings.
- Live collector run 1: ~9m23s, clean start, clean user-initiated shutdown (exit 130 on Ctrl+C, expected).
- Live collector run 2 (post-restart): checkpoint unchanged, 0 duplicate rows.
- Manual content spot-check: 20/500 rows, all correct (including timezone display clarified as UTC storage, not a defect).

Full detail in [test-evidence.md](test-evidence.md).

## 6. Open Items

- Product Critical: 0.
- Product Major: 0.
- Product Minor: 0.
- Gate-blocking evidence gaps: 5 (see [known-issues.md](known-issues.md) NOTVERIFIED-001 through 005).
- User acceptance has not been recorded.

## 7. Gate Decision

`NOT READY, RISK-ACCEPTED BY USER`. Per [phase-kickoff.md](phase-kickoff.md) Development Sequence, Phase 2 remains at step 6 (verifying backfill/edit/media/dedup/checkpoint/reconnect) and did not complete step 7 (24-hour soak / full Gate 2 package). The user was shown the specific unmet criteria and the resulting conflict with [ADR-0001](../adr/0001-sequential-stage-gates.md), and explicitly chose to proceed to Phase 3 anyway, with the gaps recorded as accepted risk rather than as passed evidence. These gaps are carried forward into Phase 3 as open risk items.
