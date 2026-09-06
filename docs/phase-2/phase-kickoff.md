# Phase 2 Kickoff — Telegram Read-only Collector

## Status

- Phase: `Phase 2 — Telegram Read-only Collector`
- Status: `IN PROGRESS`
- Gate 2: `NOT EVALUATED`
- Baseline: `phase-1-accepted`
- Authorized by: User
- Authorization date: `2026-09-06`
- Active branch: `phase/2-telegram-readonly-collector`

## Objective

以 Telegram MTProto 與 Telethon 建立唯讀 Collector，驗證訊息可被完整、穩定且安全地接收、保存與續傳。本階段不解讀交易語意，也不產生任何交易決策。

## Initial Channel Scope

- channel_label: `Monster-貨幣宇宙中心`
- public_username: `@followgerry`
- channel_type: `EXECUTION_SIGNAL`
- onboarding policy: Phase 2 僅使用此頻道；系統完成後續開發且穩定前，不加入其他頻道。

## In Scope

- `NewMessage`
- `EditedMessage`
- reply 與 forward 關係保存
- text 與 caption 擷取
- 圖片下載與 media hash
- History backfill
- Channel dialog listing
- Reconnect and retry
- 以 `(channel_id, message_id, edit_version)` 去重
- Collector checkpoint 與 controlled restart recovery
- 唯讀 audit trail 與操作狀態檢查

## Out of Scope / Prohibited

- 不解析交易訊號。
- 不建立 `NormalizedSignal` 或 `TradeIntent`。
- 不接 Binance private/public trading API。
- 不送資料給 OpenAI 或其他 AI provider。
- 不建立 Control Bot。
- 不計算風險、倉位或下單數量。
- 不模擬或執行任何真實下單。
- 不提前開發 Phase 3 及後續功能。

## Credential Introduction Contract

Phase 2 僅允許以下資料透過使用者本機環境引入：

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- Telethon session

安全規則：

- 手機號碼、登入驗證碼與 2FA 密碼只由使用者在本機 terminal 互動輸入。
- 不得把手機號碼、驗證碼、2FA、API hash 或 session 貼到聊天。
- Secret 不得寫入 repository、tracked file、log、exception、screenshot 或測試報告。
- Session 檔案必須位於 Git ignore 的本機資料目錄，且權限採最小化。
- 所有 credential log 欄位必須遮罩；測試使用 fake values。
- Production Binance、Binance Testnet、OpenAI 與 Control Bot credentials 在本階段均禁止配置。

Kickoff 時尚未提供、儲存或引入任何真實 credential。

## Baseline Validation Note

Phase 2 開工驗證在 Windows 中文工作路徑發現 CI portability 問題：`uv run` 重新同步 editable package 會產生 locale-sensitive `.pth`、Windows PowerShell 對 native command 非零 exit code 未可靠 fail-fast，且不同本機執行身分可能共用無權存取的 pytest temp/cache。Phase 2 分支在功能開發前先將所有 post-sync command 固定為 `uv run --no-sync`、逐步檢查 native exit code，並隔離 pytest temp/cache，避免後續 Gate 證據出現假成功。`phase-1-accepted` tag 保持不變，修正紀錄由本分支承接。

## Development Sequence Within Phase 2

1. 建立 Phase 2 config contract、依賴與 credential-safe login/bootstrap。
2. 以 fake client／fixtures 完成 Collector 單元與整合測試。
3. 使用者在本機 terminal 完成 Telegram 互動登入。
4. 執行 dialog listing，確認唯一允許的目標頻道 ID。
5. 啟用單一頻道的 live read-only collection。
6. 驗證 backfill、edit version、media、去重、checkpoint 與 reconnect。
7. 執行 24 小時 soak，產出 Gate 2 acceptance package。

每一步若測試失敗，留在原步驟修正與重測，不得跨入下一步或 Phase 3。

## Gate 2 Required Evidence

- 連續 24 小時 read-only 運行證據。
- 對 `@followgerry` 人工比對，訊息 ID 與數量一致。
- controlled restart 後無漏訊、無重複。
- 編輯訊息保留原版本與新版本。
- 僅讀取 Collector Account 已合法加入或可合法存取的內容。
- Telegram session 不出現在 log、Git 或測試報告。
- Phase Report、Test Evidence、Requirement Traceability、Security Check、Known Issues 與 Gate Verdict。
- Critical = 0、Major = 0，且所有 Phase 2 Acceptance Criteria 通過。
- 使用者明確驗收前，不得開始 Phase 3。

## Current Decision

Phase 2 已獲准開始，但 Gate 2 尚未評估。本文件只建立階段邊界與安全契約，不代表 Telegram live connection、24 小時 soak 或 Gate 2 已完成。
