# Domain Context

## Scope

本 glossary 定義 Telegram 頻道內容由合法存取、正規化、訊號判讀、風險審核到 Binance Futures 訂單生命週期所使用的共通語言。它只定義業務意義；資料表、API 與模組設計分別由 System Specification 與 Architecture 文件管理。

## Terms

### Source Channel

- **Definition:** Collector Account 已合法加入或可合法存取、且被系統登錄為資料來源的 Telegram channel。
- **Not:** Control Bot 私聊、測試 fixture、任意可搜尋的公開貼文。
- **Relationships:** Channel Policy、Collector Account、Raw Message。
- **Example:** 使用者帳號已加入的某個加密貨幣分析頻道。

### Channel Policy

- **Definition:** 對單一 Source Channel 的用途、授權、允許 symbol、內容類型、保存期與執行模式所作的版本化規則。
- **Not:** 頻道作者本人的交易策略，也不是 Telegram 權限設定。
- **Lifecycle / States:** `DRAFT → MONITOR_ONLY → ENABLED → PAUSED → RETIRED`。
- **Relationships:** Source Channel、Authorization Status、Strategy Contract。
- **Example:** 某頻道被標記為 `EXECUTION_SIGNAL`，只允許 `BTCUSDT` 與 `ETHUSDT`。

### ANALYSIS Channel

- **Definition:** 內容提供市場 thesis、情境或觀察條件的 Source Channel；即使條件成立，也只能建立待人工核准的候選交易。
- **Not:** 可自動下單的訊號來源。
- **Relationships:** Thesis、Strategy Contract、Candidate Trade。
- **Example:** 作者認為 BTC 回踩支撐後偏多，系統監控條件成立後通知使用者。

### EXECUTION_SIGNAL Channel

- **Definition:** 經白名單與授權 Gate 核准、可將明確 `symbol + side` 轉成 Trade Intent 的 Source Channel。
- **Not:** 無條件照單全收；每筆仍須通過新鮮度、衝突與 Risk Gate。
- **Relationships:** Normalized Signal、Trade Intent、Risk Gate。
- **Example:** 頻道文字明確寫出 `BTCUSDT LONG`，且未被取消或否定。

### Raw Message

- **Definition:** 從 Telegram 接收的不可覆寫來源事件，包含來源識別、message ID、版本、時間、文字、caption、reply/forward metadata 與 media reference。
- **Not:** 已解析訊號或可直接下單的資料。
- **Lifecycle / States:** `RECEIVED → PERSISTED`；編輯產生新版本，不能覆寫舊版本。
- **Relationships:** Normalized Content、Media Object、Audit Event。
- **Example:** Telegram message 123 的第一次內容與後續 edit 分別保存為兩個版本。

### Normalized Content

- **Definition:** 對 Raw Message 做 deterministic 清理、語言與 symbol alias 正規化後的內容。
- **Not:** 交易方向判斷或 AI 結論。
- **Relationships:** Raw Message、Normalized Signal、Thesis。
- **Example:** `BTC/USDT` 被正規化為 `BTCUSDT`，原文證據仍保留。

### Normalized Signal

- **Definition:** 從來源證據抽取出的版本化訊號結構，至少記錄解析狀態、symbol、side、entry、SL、TP、evidence spans 與來源訊息。
- **Not:** 訂單、持倉或已通過風控的交易指令。
- **Lifecycle / States:** `NEW → INCOMPLETE | VALIDATED | CANCELLED | EXPIRED | SUPERSEDED`。
- **Relationships:** Signal Link、Trade Intent、Raw Message。
- **Example:** `BTCUSDT LONG` 無 SL，狀態為 `VALIDATED` 且 stop origin 為 `DEFAULT_ROE_30`。

### Thesis

- **Definition:** 從 ANALYSIS Channel 抽取、可由明確市場條件支持或推翻的作者觀點。
- **Not:** 系統自行創造的交易策略或進場許可。
- **Lifecycle / States:** `DRAFT → MONITORING → CONFIRMED | INVALIDATED | EXPIRED | INSUFFICIENT_DATA`。
- **Relationships:** Strategy Contract、Market Snapshot、Candidate Trade。
- **Example:** 「BTC 回踩 60,000 且 1H 收回才偏多」被保存為條件化 thesis。

### Strategy Contract

- **Definition:** 把特定 ANALYSIS Channel 的模糊語句映射為可測試市場條件的版本化規則。
- **Not:** LLM prompt 或未經驗證的自由推理。
- **Relationships:** Thesis、Market Confirmation、Channel Policy。
- **Example:** 定義何謂「站回壓力位」及使用哪一個閉合 candle。

### Market Confirmation

- **Definition:** 以具時間戳、來源與 freshness 的市場資料判斷 Strategy Contract 是否成立。
- **Not:** 頻道作者說法本身，也不是 AI confidence。
- **Lifecycle / States:** `WAIT | CONFIRMED | INSUFFICIENT_DATA | INVALIDATED`。
- **Relationships:** Market Snapshot、Strategy Contract、Candidate Trade。
- **Example:** Binance 1H closed candle 符合版本化條件後標記 `CONFIRMED`。

### Candidate Trade

- **Definition:** ANALYSIS 路徑在 Market Confirmation 成立後產生、只能由使用者核准或拒絕的候選。
- **Not:** 可直接送至 Binance 的 Trade Intent。
- **Lifecycle / States:** `PENDING_APPROVAL → APPROVED | REJECTED | EXPIRED`。
- **Relationships:** Thesis、Trade Intent、Control Bot。
- **Example:** Control Bot 顯示候選，多單核准倒數 120 秒。

### Trade Intent

- **Definition:** 具有唯一 idempotency key、來源、symbol、side、entry semantics 與風控輸入，但尚未轉成交易所訂單的交易意圖。
- **Not:** Binance Order 或已成交 Position。
- **Lifecycle / States:** `CREATED → RISK_REJECTED | RISK_APPROVED → SUBMITTED | CANCELLED | EXPIRED`。
- **Relationships:** Normalized Signal、Candidate Trade、Risk Decision、Order Lifecycle。
- **Example:** 經人工核准的 ANALYSIS 候選轉成一筆 Trade Intent。

### Risk Gate

- **Definition:** 使用 deterministic 規則審核 Trade Intent 是否可送單，並計算受硬限制的 quantity 與保護需求。
- **Not:** AI 推薦、作者勝率或人工主觀信心。
- **Relationships:** Risk Decision、Account Snapshot、Market Snapshot。
- **Example:** 第四個持倉被拒絕，即使來源訊號有效。

### Risk Decision

- **Definition:** Risk Gate 對單一 Trade Intent 的版本化 `APPROVED` 或 `REJECTED` 結果及 machine-readable reasons。
- **Not:** 實際成交結果。
- **Relationships:** Trade Intent、Order Lifecycle、Audit Event。
- **Example:** `REJECTED: DAILY_LOSS_LIMIT_REACHED`。

### Protection Order

- **Definition:** Entry fill 後在 Binance 建立、用於限制虧損或退出持倉的交易所端 conditional order。
- **Not:** 僅由本地輪詢觸發的軟性提醒。
- **Lifecycle / States:** `PENDING → CONFIRMED | FAILED | TRIGGERED | CANCELLED`。
- **Relationships:** Order Lifecycle、Position、Emergency Close。
- **Example:** 依實際均價換算 -30% position ROE 的 `STOP_MARKET`。

### Order Lifecycle

- **Definition:** Trade Intent 送到 Binance 後，entry、partial fill、fill、Protection Order、close 與 reconciliation 的完整狀態集合。
- **Not:** 單一 API response。
- **Lifecycle / States:** `PENDING_SUBMIT → SUBMITTED → PARTIALLY_FILLED | FILLED → PROTECTED → CLOSING → CLOSED`，任何狀態均可進入 `FAILED_RECONCILIATION`。
- **Relationships:** Trade Intent、Protection Order、Position、Audit Event。
- **Example:** Entry 已 fill，但 Protection Order 建立失敗，觸發 Emergency Close。

### Emergency Close

- **Definition:** 無法在時限內確認 Protection Order 時，系統為限制無保護曝險而執行的 reduce-only market close 流程。
- **Not:** 一般 TP 或使用者主動平倉。
- **Relationships:** Protection Order、System Pause、P0 Alert。
- **Example:** Entry fill 後 5 秒仍無保護單，系統平倉並暫停新單。

### System Pause

- **Definition:** 禁止建立或送出新的 Trade Intent，但仍允許監控、reconciliation 與安全減倉/平倉的全域狀態。
- **Not:** 關閉所有服務或停止監看現有持倉。
- **Lifecycle / States:** `ACTIVE | PAUSED | EMERGENCY_PAUSED`。
- **Relationships:** Control Bot、Daily Kill Switch、Emergency Close。
- **Example:** 當日總損失達 -6% 後進入 `PAUSED`。

### Gate Decision

- **Definition:** 對開發 Phase 的 `READY | NOT_READY | USER_ACCEPTED` 判定與證據。
- **Not:** Runtime Risk Decision。
- **Relationships:** Phase Report、Test Evidence、User Acceptance。
- **Example:** Gate 0 文件完整為 `READY`，仍需使用者標記 `USER_ACCEPTED` 才能進 Phase 1。

## Unresolved Terms

| Term | Ambiguity | Intended Confirmer | Affected Work |
|---|---|---|---|
| Position ROE | Binance 帳戶畫面與內部風控應採用的精確 denominator 與 fees/funding 處理尚未以 Testnet contract spike 驗證 | Technical Owner + User | Phase 6 quantity/stop golden tests |
| 明確授權 | Telegram Content Licensing 對特定頻道、作者與 AI processing 所需同意範圍需法律/平台條款確認 | User / Legal reviewer | Phase 3、5、8 enablement |
| 低餘額子帳戶 | 可承受全損的實際 USDT 上限尚未提供 | User | Phase 8 Production Canary |
