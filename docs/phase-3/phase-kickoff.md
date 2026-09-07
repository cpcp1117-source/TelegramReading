# Phase 3 Kickoff — Channel Registry / Normalization

## Status

- Phase: `Phase 3 — Channel Registry / Normalization` (per [system-spec.md](../phase-0/system-spec.md) §14 Implementation Plan, row 3)
- Status: `KICKOFF`
- Gate 3: `NOT EVALUATED`
- Baseline: Phase 2, accepted under explicit user risk acceptance (not a full Gate 2 PASS)
- Authorized by: User, 2026-09-06
- Active branch: not yet created

## Inherited Risk From Phase 2

Phase 2 Gate 2 was **not** fully evidenced. The user explicitly authorized proceeding to Phase 3 anyway on 2026-09-06, accepting the following as open risk rather than closed evidence (full detail: [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md)):

1. No 24-hour continuous read-only run has been completed.
2. Controlled-restart "no message loss" is unproven (only "no duplication" was proven; no messages arrived during the tested restart gap).
3. No live edited-message has been observed; edit-version retention is unit-tested only, not live-verified.
4. Only 20 of 500 collected rows were manually reconciled against `@followgerry`; full reconciliation is outstanding.
5. Combined unit+integration test coverage was not re-verified at ≥85% for the Phase 2 fixed point.

This proceeding is a documented deviation from [ADR-0001 Sequential Stage Gates](../adr/0001-sequential-stage-gates.md). Because Phase 3 (and later phases) will consume the Telegram collector's stored data as its input, any undetected gap above (a missed message, a missed edit) could silently propagate into channel normalization and, eventually, signal parsing. These items should be closed as soon as practical, ideally in parallel with Phase 3 work rather than deferred indefinitely.

## Objective (per system-spec.md)

建立 Channel Policy 白名單與正規化（normalization）流程：以 Channel 類型（`ANALYSIS` / `EXECUTION_SIGNAL`）、symbol scope、authorization、freshness、retention 控制每個 Source Channel；將 Raw Message 正規化為確定性（deterministic）輸出，供後續 Phase 4 訊號解析使用。本階段**不**建立 `NormalizedSignal` 的交易語意判讀、不建立 Control Bot、不接 Binance。

## Known Open Dependency — Channel Sample Scope

[system-spec.md:351](../phase-0/system-spec.md) 列出 Phase 3 依賴為「Accepted Phase 2 + channel samples」，且 [system-spec.md:377](../phase-0/system-spec.md)（TBD-001）指出 Gate 3 前應擴充至 20 fixtures。目前 Phase 2 僅收集了單一頻道 `@followgerry` 的資料（依 Phase 2 kickoff 的範圍限制：「Phase 2 僅使用此頻道；系統完成後續開發且穩定前，不加入其他頻道」）。

**這是一個尚未解決的開放問題**：Phase 3 的 normalization 邏輯若只用單一頻道樣本開發，可能無法涵蓋其他頻道格式的變異。是否要在 Phase 3 開始前先取得更多頻道樣本（fixture），或是先以 `@followgerry` 單一頻道建立 Channel Registry 架構、之後再擴充，需要使用者決定。

## Authorization Status (per CONTEXT.md TBD-002)

`@followgerry` 的四項授權（access/automation/AI processing 等）已由使用者於 Phase 0 宣告 `GRANTED`，本階段可沿用。**未來新增的任何頻道，都必須重新完成 onboarding 與授權宣告，不得沿用 `@followgerry` 的授權。**

## In Scope (proposed, pending user confirmation)

- Channel Policy 白名單 schema（type、symbol scope、authorization、freshness、retention）
- Raw Message → Normalized 輸出的確定性轉換（text/caption normalization、symbol alias）
- Channel type 分離：`ANALYSIS` vs `EXECUTION_SIGNAL`（`BR-001`：不得以內容臨時互換）

## Out of Scope / Prohibited

- 不建立 `NormalizedSignal` 交易語意判讀或 `TradeIntent`（屬 Phase 4）。
- 不建立 Control Bot（屬 Phase 4）。
- 不接 Binance API。
- 不送資料給 OpenAI 或其他 AI provider（屬 Phase 5）。
- 不為新頻道跳過 onboarding/授權流程。

## Current Decision

Phase 3 已獲使用者授權開始（風險承擔前提下），但尚未建立分支、尚未撰寫任何 Phase 3 程式碼。本文件只記錄範圍邊界與繼承風險，實作開始前建議使用者先確認上述「Channel Sample Scope」的開放問題。
