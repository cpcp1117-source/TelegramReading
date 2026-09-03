# Telegram Channel Trading Monitor

本專案採嚴格 Sequential Stage Gate。任何 Phase 未取得 `READY + User Accepted` 前，不得開始下一 Phase。

## Current Status

- Active phase: `Phase 0 — Requirements and Architecture`
- Gate status: `NOT_READY`（`@followgerry` 已登錄；待確認 Phase 0 初始頻道清單是否完整）
- Runtime code: 尚未建立
- External credentials: 不需要，且不得加入工作區
- Git branch: `phase/0-requirements-architecture`
- Git remote: `https://github.com/cpcp1117-source/TelegramReading.git`
- Next permitted action: 確認 `@followgerry` 是否為初始唯一頻道；若不是，提供其餘頻道資料後完成 Gate 0 re-review

## Phase 0 Deliverables

- [System Specification](docs/phase-0/system-spec.md)
- [Architecture and Data Flow](docs/phase-0/architecture.md)
- [Logical Data Model](docs/phase-0/logical-data-model.md)
- [Channel Onboarding Template](docs/phase-0/channel-onboarding-template.md)
- [Monster-貨幣宇宙中心 Onboarding Record](docs/phase-0/channels/monster-currency-universe.md)
- [Credential Handoff Procedure](docs/phase-0/credential-handoff.md)
- [Threat Model](docs/phase-0/threat-model.md)
- [Test Strategy](docs/phase-0/test-strategy.md)
- [Acceptance Traceability Matrix](docs/phase-0/acceptance-traceability.md)
- [Telegram / Binance API Contract Inventory](docs/phase-0/api-contract-inventory.md)
- [Phase Report](docs/phase-0/phase-report.md)
- [Gate 0 Delivery Quality Review](docs/phase-0/quality-review.md)
- [Gate 0 Checklist](docs/phase-0/gate-0-checklist.md)
- [Domain Context](docs/phase-0/CONTEXT.md)
- [Architecture Decision Records](docs/adr/)

## Gate Rule

Gate 0 目前因缺少使用者 Required Inputs 為 `NOT_READY`。補齊並通過 re-review 後，仍只有在使用者明確回覆批准時才會標記為 `User Accepted`。在此之前禁止建立 Python package、database migration、Docker Compose、API client 或任何交易功能。
