# 分析型頻道（Monster、邦妮）導入：現況與方案

- 日期：2026-09-30
- 狀態：**已決策（2026-09-30）**，見第 7 節
- 起因：使用者想知道下面這類訊息進到系統後，會怎麼開單

## 1. 樣本訊息

| # | 來源 | 內容摘要 |
|---|---|---|
| 1 | Monster 頻道 | `#NMR 風險短多` |
| 2 | 邦妮頻道（幣種追蹤分享） | LINK：小級別結構轉換、壓力 w vah 15–15.3、反彈受阻先看下行、支撐 14.3、14–13.8、籌碼偏空「開空平多」、現貨 CVD 背離、留意回調 |
| 3 | 邦妮頻道（幣種追蹤分享） | LTC：小時結構向下、跌破 m vah、壓力 m vah–w vwap 68.8–70「受阻可嘗試介入」、不突破 pw vah 先看下行、目標 65.8、64–63.5、62、「尾段有平空 先留意反彈」 |

## 2. 現況：目前系統怎麼處理

**這兩個頻道都還沒加入系統。** `channel_policies` 裡只有 `@followgerry`，collector 不會讀其他頻道，所以現在收到這三則訊息，什麼都不會發生。

下面是「**假設**把它們當成 `EXECUTION_SIGNAL`（信號頻道）加進來」的結果。這是把原文丟進實際的 `normalization.normalize_text`、`signal_parser.parse_fields`、`resolve_stop` 跑出來的，不是推測。

| # | 幣種 | 方向 | 進場 | 停損 | 停利 | 結果 |
|---|---|---|---|---|---|---|
| 1 NMR | NMR | LONG（抓到「多」） | MARKET | `DEFAULT_ROE_30` | 無 | 通知使用者核准 |
| 2 LINK | LINK | **None** | — | — | — | `INCOMPLETE`，**不通知**，直接略過 |
| 3 LTC | LTC | SHORT（抓到「平**空**」的「空」） | MARKET | `DEFAULT_ROE_30` | [65.8, 64] | 通知使用者核准 |

### 各則的問題

**1. NMR**
- 「風險」這個警告被忽略，照樣用完整倉位：`RISK_POSITION_SIZE_PCT` 0.20 的保證金、5 倍槓桿，名目價值等於 100% 資金。
- 沒寫停損，用預設 −30% ROE，也就是成交價下方 6%。打到停損大約虧 6% 資金，等於每日熔斷上限（`RISK_DAILY_LOSS_KILL_SWITCH_PCT` 0.06）。
- 沒有停利。

**2. LINK**
- 「開空平多」同時出現「空」和「多」，`_extract_side` 判定方向不明確（刻意的 fail closed），所以狀態是 `INCOMPLETE`。
- Control Bot 只會通知 `NEW` 和 `VALIDATED`（`signal_decisions.load_pending_signals`），所以使用者看不到這則。
- 原文其實是偏空的條件式看法。

**3. LTC：方向是誤打誤撞**
- **方向**：唯一抓到的「空」來自「尾段有**平空**，先留意反彈」。這句的意思是空單在平倉，偏多、要小心，但系統判成 SHORT。剛好跟整篇偏空一致，屬於巧合。
- **進場**：原文是條件式的「68.8–70 **受阻可嘗試介入**」，系統會**立即市價做空**，不看現價在哪裡。
- **停損**：原文暗示停損在壓力區上方（70／pw vah），系統用的是成交價上方 6%。
- **停利**：只抓到 65.8、64，漏了 63.5、62（`_NUMBER_WINDOW = 20`，只看關鍵字後 20 個字元）。Execution Gateway 只會用 `take_profits[0]`，全部數量一次出場（P6-NOTICE-015）。

### 根本原因

`signal_parser` 是為 `@followgerry` 這種「幣種＋方向＋價格」的**明確指令**設計的規則式解析器，只抓關鍵字，不懂條件、區間和語意。邦妮是**條件式的區間分析**，Monster 是帶有風險警告的短句。規格書原本就把這類內容歸在 **ANALYSIS 頻道**：由 AI 萃取重點，不直接轉成下單。

## 3. 相關的既有設計與限制

| 項目 | 現況 | 位置 |
|---|---|---|
| 頻道分類 | `channel_policies.channel_type`：`EXECUTION_SIGNAL` 走規則解析，`ANALYSIS` 走 AI 萃取 | `parse_signals.load_signal_targets` |
| AI 萃取（FR-012） | **已寫好但從沒跑過真實資料**。輸出 `thesis`：`primary_direction`（BULLISH/BEARISH/NEUTRAL）、`confidence_status`、`evidence_quotes`、`conditions`（comparator ABOVE/BELOW、trigger_price、symbol、implication UP/DOWN/NEUTRAL） | `thesis_extraction.py`、schema `t1` |
| Thesis 的下游（FR-013/014） | **沒有實作**：Strategy Contract、Market Confirmation、Candidate Trade 都不存在，Thesis 目前是沒人用的資料，Control Bot 也不會通知 Thesis | Gate 5 風險接受，見 `docs/phase-5/gate-5-checklist.md` |
| 每頻道的授權 | 新頻道要重新 onboarding，**不能沿用** `@followgerry` 的授權（內容讀取、AI 處理等四項） | TBD-002、`ai_authorization.py` |
| 每頻道的策略規則 | 還沒定 | TBD-004 |
| AI 服務與資料保留 | 目前是 OpenAI（`THESIS_MODEL=gpt-4o-mini`），條款還沒定案 | TBD-005 |
| 使用者編修 | Control Bot 已經有「編輯停損／停利」按鈕，核准前可以改 | `control_bot` 的 edit callback |
| 缺少的編修 | **不能改進場價**，也不能把市價改成限價 | — |
| 風控 | 只接受 `VALIDATED` 的信號；固定 20% 保證金、5 倍槓桿；BR-006 價格偏離檢查 | `risk_engine.py`、P6-NOTICE-007 |
| 系統原則 | 半自動：失敗只通知，由使用者處理；每筆都要使用者核准才會下單 | memory：semi-automatic-failure-policy |

## 4. 方案

### 方案 A：維持現狀，不加入
- 優點：零風險、零工作量。
- 缺點：用不到這兩個頻道的資訊。

### 方案 B：加入為 ANALYSIS 頻道，AI 整理後由使用者決定（建議）

收到訊息後，用 AI 整理成結構化摘要推到 Telegram，例如：

```
📊 LTC（邦妮）偏空 · 信心：中
條件：68.8–70 受阻 → 做空
失效：突破 pw vah
目標：65.8 / 64–63.5 / 62
提醒：尾段有平空，留意反彈
[建立交易草稿] [忽略]
```

使用者覺得可以做，就按「建立交易草稿」，自己填或修改方向、進場（市價或限價）、停損、停利、倉位比例，再核准。之後沿用現有的風控、下單、停損停利、平倉通知流程。

- **優點**：
  - 最符合半自動原則，AI 只負責整理，不負責決策。
  - 條件式、區間式的內容可以完整保留。
  - 誤判不會直接變成下單。
- **要做的事**：
  1. 兩個頻道的 onboarding 和授權（TBD-002），包括收集樣本 fixtures。
  2. 在真實資料上跑 Thesis 萃取並驗證品質。可能要調整 schema：加上區間、失效條件、多個目標、風險註記。
  3. Control Bot 新增 Thesis 通知和「建立交易草稿」流程。
  4. **可以改進場價，也可以改成限價**（目前做不到）。
  5. 可以選倉位大小，例如「風險短多」要能用較小的倉位。
  6. 決定 TBD-005（AI 服務與資料保留），以及 AI 費用上限。
- **暫時可以不做**：FR-013/014 的自動 Market Confirmation，也就是自動判斷價格有沒有到壓力區。先讓使用者自己判斷；之後想要的話，再加「價格到達條件區時提醒」。

### 方案 C：加入為 EXECUTION_SIGNAL，修改規則式解析器
- 要修的地方：
  - 看懂「平空／平多／開空平多」是在描述籌碼，不是交易方向。
  - 辨識「受阻可嘗試」「不突破」這類條件式句子。
  - 支援區間價格（68.8–70、64–63.5）。
  - 放寬停利數字的搜尋範圍，並支援多個停利。
  - 抓出「風險」這類警告，對應成較小的倉位。
- **缺點**：
  - 規則式解析器很難涵蓋這種自由寫法，而且誤判會直接變成可核准的單。
  - 每筆都還是要使用者手動改進場價和停損才能用，跟方案 B 相比沒有省下多少工作。
- 可以當成方案 B 的補充：只對很短的明確句型（像 Monster 的 `#NMR 風險短多`）走規則解析。

## 5. 新對話要先決定的事

1. 選哪個方案（A、B、C，或 B＋C 混合：Monster 走規則解析、邦妮走 AI）。
2. 兩個頻道的實際 Telegram 帳號或 ID，以及授權的四項宣告（TBD-002）。
3. 要不要提供 10～20 則歷史訊息當樣本 fixtures（Gate 3 的慣例）。
4. AI 服務：沿用 OpenAI `gpt-4o-mini`，或換成其他服務；可接受的每月費用。
5. 分析型交易要不要用不同的倉位大小，或是每筆可以自己選。
6. 同一幣種同時有 `@followgerry` 的信號和分析型交易時怎麼處理。目前 BR-007 只會提醒，不會擋。

## 6. 新對話的開場提示（可直接貼上）

> 請讀 `docs/proposals/analysis-channels-handoff.md`。我要討論 Monster 和邦妮兩個分析型頻道怎麼導入這個半自動交易機器人。先幫我釐清第 5 節的決策點，再提出實作計畫。

## 7. 決策紀錄（2026-09-30）

- 方案：**B＋C 混合**。Monster 走規則解析（只收短句型，例如 `#NMR 風險短多`），邦妮走 ANALYSIS／AI 萃取，另外新增條件監控器。
- 條件成立後：**推送預填草稿，等使用者核准**，不自動下單。
- 條件判定：**兩段通知**。價格碰到區間先提醒一次，K 線收盤確認「受阻」或「突破」後再推送草稿。碰到失效價或過期就取消監控並通知。
- 倉位：**每筆自己選，預設較小**。有「風險」字樣的單預設更小。
- AI 服務：沿用 OpenAI `gpt-4o-mini`。
- 還沒決定：兩個頻道的帳號或 ID、樣本 fixtures、確認用的 K 線週期、監控有效期、預設倉位比例、TBD-005 資料保留條款。

## 8. 實作狀態（2026-10-04，分支 `feat/analysis-setup-monitor`）

已完成並測試：

- AI 呼叫可切換：`AI_PROVIDER=codex` 用本機已登入的 Codex CLI（不需要 API key），預設模型 `gpt-5.6-sol`；`openai` 維持原行為。
- `analysis_setups` 資料表與抽取流程（`setup_extraction.py`）：AI 只負責描述（幣種、方向、進場區、停損、目標、可選的跌破條件）；讀訊息時會一併帶入同討論串前 8 則，補充型訊息（沒寫幣種）也能判斷。AI 輸出由 `analysis_setup.parse_setup_response` 驗證（幣種必須在 Binance 永續清單、引用必須逐字出現在輸入、停損與目標必須在進場區的正確一側）。
- 市場監控（`setup_monitor.py`，不用 AI）：兩段通知。碰到進場區 → 「碰到進場區」提醒；K 線收盤確認 → 建立 `VALIDATED` 信號，由既有 Control Bot 核准 → 風控 → 下單流程接手。停損先被碰到、先到目標（原文要求撤單時）、逾時 48 小時 → 結束並通知。
- 確認規則由程式決定：AI 給的「跌破 X」若與方向一致就用「收盤跌破／站上 X」，否則用預設的「碰到區間後 K 線反轉收盤」。
- 通知：Control Bot 轉送 `setup_*` 事件。
- 收兩個邦妮頻道：原本的 topic 21（BTC／ETH）與 VIP 的 topic 7（`docs/phase-0/channels/bonnie-vip-club.md`）。
- 常駐程式：`python -m telegram_trader.setup_worker`（要在**主機**執行，因為 Codex 登入在主機；容器裡沒有）。`--once` 只跑一輪。

已知限制：

- 草稿的進場價是限價單，目前 Control Bot 還不能改進場價（只能改停損／停利），也不能改倉位比例。
- Execution Gateway 只用第一個停利（`take_profits[0]`），多個目標不會分批出場。
- 主機要連資料庫：`compose.yaml` 的 `db` 沒有對主機開 port。
- Monster 頻道仍走原本的規則解析，尚未處理「風險」字樣縮小倉位。

## 9. 常駐執行（Windows 工作排程器）

`powershell -File scripts\register-setup-worker-task.ps1` 會註冊工作 `TelegramTrader-SetupWorker`：登入時自動啟動、隱藏視窗、無時間限制、掛掉自動重啟；`-Remove` 可移除。執行腳本是 `scripts/run-setup-worker.ps1`，日誌在 `logs/setup-worker.log`（超過 20MB 輪替）。前提：資料庫要用 `compose.host-db.yaml` 對本機開 port，`codex` 保持登入。

注意：工作排程器的指令列會弄壞含中文的專案路徑，所以工作指向 `%USERPROFILE%\.telegram-trader\` 下的 ASCII 轉接腳本；不能放在 AppData（桌面 App 的 AppData 是虛擬化的，排程工作看不到）。
