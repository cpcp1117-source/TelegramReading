# Telegram Channel Trading Monitor — Integrated System Specification

## 1. Document Summary

- **System / Feature Name:** Telegram Channel Trading Monitor
- **Specification Version:** v0.1
- **Date:** 2026-09-02
- **Target Audience:** User / Acceptance Owner、Product/Technical Architect、Backend Engineer、QA、Security/Operations Reviewer
- **Source Basis:** 使用者確認的 Sequential Development and Stage-Gate Plan、Phase 0 discovery、Telegram/Binance 官方文件
- **Document Status:** Draft complete；Gate 0 `NOT_READY`，等待 Channel Onboarding Required Inputs
- **Implementation State:** Greenfield；沒有 runtime code、database、Docker 或 external credentials

### Revision History

| Version | Date | Author / Owner | Change Summary | Status |
|---|---|---|---|---|
| v0.1 | 2026-09-02 | Codex / User | Phase 0 初版整合規格 | Ready for review |

### Document Conventions

- Requirement IDs: `FR-001`、`BR-001`、`NFR-001`、`AC-001`
- Priority: Must / Should / Could / Won't
- `Confirmed`: 已由使用者決策或官方 contract 支持
- `Assumption`: 可逆但尚待實測的設計假設
- `TBD`: 必須在指定 Gate 前由 owner 解決
- 此文件定義需求；架構、logical data model、測試矩陣與 external API details 由 Phase 0 對應文件作為權威附件。

### Authoritative Project Documents

- [Domain Context](CONTEXT.md)
- [Architecture](architecture.md)
- [Logical Data Model](logical-data-model.md)
- [API Contract Inventory](api-contract-inventory.md)
- [Test Strategy](test-strategy.md)
- [Acceptance Traceability](acceptance-traceability.md)
- [ADRs](../adr/)

## 2. Background and Goals

### Background

使用者已加入若干提供加密貨幣與美股分析/訊號的 Telegram channels，但無法持續人工監看。系統需要以 Telegram User Account 讀取該帳號合法可見的內容，將來源保存及結構化，對分析內容監控市場條件，並在嚴格風控下支援 Binance USDⓈ-M Futures。由於最終可能觸及真實資金，開發必須採 Sequential Stage Gate，先證明 ingestion、解析、風控、Testnet 與復原能力，再進 Production Canary。

### Goals

1. 完整、可追溯地接收白名單 Telegram channels 的 text、caption、edit、reply/forward metadata 與圖片。
2. 清楚分離 `ANALYSIS` 與 `EXECUTION_SIGNAL` 兩條路徑。
3. 讓 AI 僅處理獲授權的非結構化分析，不具交易權限。
4. 以 deterministic Risk Gate 保護 Binance Futures 下單。
5. 以 Control Bot 提供通知、人工核准、pause、resume 與緊急平倉控制。
6. 每個 decision、state transition、API request result 與人工操作均可稽核及 replay。
7. 每個開發 Phase 經測試、Quality Gate 與使用者驗收後才進下一 Phase。

### Non-goals

- Phase 0 不建立任何 runtime code 或 external connection。
- MVP 不支援美股券商下單；美股內容最多保留作未來需求研究。
- MVP 不支援 Spot、COIN-M Futures、Options、Hedge Mode 或 Cross Margin。
- 不繞過 Telegram membership、private channel 或 content licensing 限制。
- 不用 Telegram 內容訓練、fine-tune、benchmark 或建立 AI dataset。
- 不讓 LLM 自主產生無來源 symbol、side、entry、SL、TP、quantity 或交易指令。
- 不在 Gate 7 前使用 Production Binance credential；不在 Gate 8 前開真實自動交易。
- 不宣稱任何頻道、策略或 AI 具有獲利能力。

### Product Perspective

系統是 single-user、source-governed 的交易監控與受控執行平台，不是多租戶 SaaS。MVP 以固定 VPS、Docker Compose、PostgreSQL 與少量分離服務滿足 secrets isolation、replay 與 recovery；不導入 Kubernetes 或獨立 message broker。

## 3. Scope

### In Scope

- Sequential Phase 0–8 governance
- Telegram MTProto User Authorization 與 read-only collection
- Channel Registry、content authorization 與 retention
- Text/caption normalization、symbol alias、OCR/Vision manual-review path
- Deterministic EXECUTION_SIGNAL parser 與 signal lifecycle
- Authorized ANALYSIS extraction、versioned Strategy Contract 與 market confirmation
- Private Control Bot
- Binance public market data、Testnet、USDⓈ-M Futures One-way/Isolated 5x
- Position sizing、daily kill switch、Protection Order、Emergency Close、reconciliation
- Append-only audit、monitoring、backup/restore 與 Production Canary

### Out of Scope

- 未經 Channel Policy allowlist 的來源
- 未加入/無權存取的 private channels
- 聲音、影片、外部網站全文爬取
- 自動執行 image-only trading instruction
- 多使用者、多策略資金分帳
- 完整 dashboard；MVP 操作介面為 Private Control Bot
- Portfolio optimization、績效保證、投資建議適合性評估

### Dependencies and Constraints

- Collector Account 必須已合法加入 private channel；Telegram `messages.getHistory` 對未加入的 channel 可能回 `CHANNEL_PRIVATE`。
- Telegram updates 需要 state/gap handling；單靠 `messages.getHistory` 不能保證完整填補 channel gaps。
- AI processing 受 Telegram API Terms、Content Licensing 與來源權利人條件限制；逐 channel enablement 必須 fail closed。
- Binance Testnet 與 Production endpoints/credentials 必須硬隔離。
- Production key 必須使用低餘額 subaccount、禁止 withdrawal、VPS IP allowlist。
- 使用者是 Gate Acceptance Owner；Technical、Security、Legal owner 尚待指派。

### Operating Environment

| Area | Direction |
|---|---|
| Local development | Windows / PowerShell；Phase 1 後以 Docker Compose 提供一致環境 |
| Production | Single fixed-IP Linux VPS with Docker Compose |
| Runtime | Python async services（Phase 1 才能實作） |
| Database | PostgreSQL with transactional outbox |
| Telegram | MTProto User Client + separate Bot API Control Bot |
| Market/Execution | Binance USDⓈ-M Futures public data / Testnet / Production Canary |
| AI | Provider adapter；只對明確授權 channel 啟用 |

## 4. Roles and Use Cases

| Role | Goal | Main Scenarios | Permission / Limitation |
|---|---|---|---|
| User / Acceptance Owner | 管理來源、核准 ANALYSIS 候選、控制系統、驗收 Gate | Approve/Reject、Pause/Resume、Close、accept phase | 唯一 Control Bot allowlist user；不得繞過 Risk Gate |
| Collector Account | 接收合法可見 Telegram 內容 | New/edit/history/media | 沒有 Binance 或 AI credential |
| Channel Content Owner | 提供來源內容與可能的 processing permission | Content authorization | 不等同系統操作員 |
| Orchestrator | 正規化、解析、thesis、signal lifecycle | Build normalized records | 沒有 Binance credential |
| Risk Gate | 決定 Trade Intent 是否可送出 | Approve/reject with reasons | Deterministic；不接受 LLM 覆寫 |
| Execution Gateway | 與 Binance 互動 | Submit/query/cancel/protect/reconcile | 唯一持有 Binance credential 的 service |
| Control Bot | 對 User 呈現與接收命令 | Status、approval、pause、close | 只接受 allowlisted numeric user ID |
| External Provider | Telegram、Binance、AI provider | API responses/events | 視為不可靠 network dependency |

### Stimulus / Response Sequences

| Scenario | Stimulus | System Response | Exception Handling |
|---|---|---|---|
| New EXECUTION_SIGNAL | 白名單 channel 發布可驗證 `symbol + side` | 建立 Normalized Signal、freshness/conflict/risk checks、合格才產生 Trade Intent | 缺欄位、否定、stale、衝突均 reject/notify |
| New ANALYSIS | 授權 channel 發布 thesis | 抽取含 evidence 的 Thesis，依 Strategy Contract 監控 | 未授權不送 AI；條件不足為 `WAIT` |
| ANALYSIS confirmed | 市場條件成立 | 建立 Candidate Trade，等待 Control Bot approval | approval expired/rejected 則不建立 Trade Intent |
| Entry filled | Binance order fill | 依實際均價建立 Protection Order | 5 秒未確認則 Emergency Close + pause |
| Follow-up | Channel 發布 TP/SL/close 更新 | 明確 link 才更新對應 lifecycle | 模糊 link 轉人工，不以同 symbol 猜測 |
| Daily loss reached | 當日 realized + unrealized <= -6% equity baseline | 進入 System Pause，禁止新單 | 仍監控現有倉位並允許安全平倉 |
| Phase completed | Test evidence ready | 產出 Gate Verdict | 無明確 User Accepted 不得進下一 Phase |

## 5. Functional Requirements

| ID | Requirement | Priority | Observable Acceptance |
|---|---|---|---|
| FR-001 | 系統必須保存 Phase、Gate Verdict、test evidence 與 user acceptance；未 `USER_ACCEPTED` 禁止啟用下一 Phase capability。 | Must | Gate enforcement test 無法啟用 phase N+1 flag |
| FR-002 | 系統必須以 Channel Policy 白名單控制每個 Source Channel 的 type、symbol、authorization、freshness 與 retention。 | Must | 未登錄 channel fail closed |
| FR-003 | Collector 必須支援 Telegram new message、edit、text、caption、reply/forward metadata 與圖片 reference。 | Must | Gate 2 source comparison 通過 |
| FR-004 | Collector 必須保存 update state、偵測 gap，依 Telegram contract 進行 difference/message recovery。 | Must | Controlled gap/restart 無漏訊 |
| FR-005 | Raw Message 必須以 channel/message/version 唯一化；edit 只能新增版本，不可覆寫歷史。 | Must | Replay/edit tests 無重複且歷史可見 |
| FR-006 | Media 必須以 content hash 去重，保存來源與 scan/processing status。 | Must | 相同 media 不重複保存 |
| FR-007 | Normalizer 必須保留原文 evidence，同時正規化 symbol alias、時間與 text/caption。 | Must | Fixture output deterministic |
| FR-008 | 未通過 access/automation/AI authorization 的 Channel Policy 必須阻擋對應 processing path。 | Must | Unauthorized path tests 全部 fail closed |
| FR-009 | EXECUTION_SIGNAL parser 必須辨識 symbol、side、entry、SL、TP、cancel/close 與 negation，並保存 evidence spans。 | Must | Gate 4 fixture matrix 通過 |
| FR-010 | EXECUTION_SIGNAL 缺 symbol 或 side 必須為 `INCOMPLETE`；缺 SL 可使用 `DEFAULT_ROE_30`，不得猜測作者價位。 | Must | Missing-field tests 通過 |
| FR-011 | Follow-up 只有 reply、message link、signal ID 或唯一 lifecycle 可明確配對時才可自動更新。 | Must | Ambiguous update 轉人工 |
| FR-012 | ANALYSIS path 必須輸出 schema-valid Thesis、evidence、conditions、invalidation 與 confidence/missing-data status。 | Must | Unknown-field/evidence tests 通過 |
| FR-013 | Strategy Contract 與 Market Confirmation 必須版本化，並以 fresh market data 輸出 `WAIT/CONFIRMED/INSUFFICIENT_DATA/INVALIDATED`。 | Must | Replay 與 stale tests 通過 |
| FR-014 | Candidate Trade 只能經 allowlisted User 明確 approval 轉為 Trade Intent；approval 必須有 expiry。 | Must | Unauthorized/expired approval 不產生 intent |
| FR-015 | Control Bot 必須支援 status、signals、positions、pause、resume、close 與二次確認 close_all。 | Must | Gate 4/7 command tests 通過 |
| FR-016 | Risk Gate 必須使用 fixed rules 計算/限制單筆風險、margin、position count、daily loss、symbol conflict 與 freshness。 | Must | Gate 6 golden/negative tests 通過 |
| FR-017 | Execution Gateway 必須只支援 Binance USDⓈ-M Futures One-way、Isolated 5x，並從 `exchangeInfo` 套用 symbol filters。 | Must | Testnet preflight/rounding tests 通過 |
| FR-018 | 每個 entry request 必須使用 deterministic unique `clientOrderId`；timeout 時 query-before-resend。 | Must | Timeout 不產生 duplicate order |
| FR-019 | Entry fill 後必須以實際 fill 建立交易所端 Protection Order，5 秒內未確認則 Emergency Close。 | Must | Protection timeout scenario 通過 |
| FR-020 | User Data Stream 與 REST reconciliation 必須重建 order、fill、position 與 protection state。 | Must | Restart reconciliation 無差異 |
| FR-021 | System Pause 必須阻止新 Trade Intent/Order，但仍允許監控、cancel 與 reduce-only close。 | Must | Pause semantics tests 通過 |
| FR-022 | 系統必須以 append-only Audit Event 記錄來源、版本、決策理由、人工操作、API outcome 與 state transitions。 | Must | End-to-end audit replay 可重建 lifecycle |
| FR-023 | Testnet 與 Production base URL、credential、database namespace 必須硬隔離，且 Testnet 不得 fallback 至 Production。 | Must | Environment isolation tests 通過 |
| FR-024 | Production Canary 必須預設 paused，只啟用單一已驗收 EXECUTION_SIGNAL channel；其他 channel disabled。 | Must | Deployment preflight 通過 |
| FR-025 | 每新增 Production channel 必須重新執行 Channel Gate 並取得 User Accepted。 | Must | 無 acceptance 無法 enable |

### Business Rules

| ID | Rule | Applies To | Related Requirements |
|---|---|---|---|
| BR-001 | Channel type 只有 `ANALYSIS` 或 `EXECUTION_SIGNAL`，不得以內容臨時互換。 | Channel onboarding | FR-002 |
| BR-002 | ANALYSIS 永遠需要人工 approval；EXECUTION_SIGNAL 在 Production Gate 後才可自動。 | Trade authority | FR-009、FR-014 |
| BR-003 | Image-derived trade instruction 永遠轉人工；不得自動執行。 | OCR/Vision | FR-006、FR-009 |
| BR-004 | Valid authored SL 優先；缺 SL 使用 default position ROE -30%；wrong-side/invalid SL 直接 reject。 | Risk | FR-010、FR-016 |
| BR-005 | 缺 TP 不得自創 TP；持倉由 Protection Order、明確 follow-up 或人工 close 管理。 | Signal/position | FR-010、FR-011 |
| BR-006 | Market-entry signal 最大 source age 60 秒、receive lag 10 秒、價格偏離 50 bps；可由 versioned Channel Policy 收緊，不得放寬超過 global hard limit。 | Freshness | FR-002、FR-016 |
| BR-007 | One-way Mode 下，同 symbol 反向 Trade Intent 為 `CONFLICT`，不得自動反手。 | Execution | FR-016、FR-017 |
| BR-008 | Leverage 固定 5x、Isolated；作者提供更高槓桿不得採用。 | Risk/Execution | FR-016、FR-017 |
| BR-009 | 單筆最大預估損失 3% account equity；單筆 initial margin 10%；總 initial margin 30%。 | Risk | FR-016 |
| BR-010 | 同時最多 3 個非零 positions；既有人工持倉也計入。 | Risk | FR-016 |
| BR-011 | Asia/Taipei 當日 realized + unrealized loss 達基準 equity -6% 即停止新單。 | Daily Kill Switch | FR-016、FR-021 |
| BR-012 | 任何必需 dependency stale/unhealthy/unknown 均 fail closed；不得以 cached success 繼續交易。 | Reliability | FR-013、FR-016、FR-020 |
| BR-013 | LLM 無 Binance credential、無 Risk Rule write access、無 execution tool。 | AI boundary | FR-012、FR-016、FR-017 |
| BR-014 | Withdrawal permission 禁止；Production key 必須為固定 VPS IP allowlist 的低餘額 subaccount key。 | Security | FR-023、FR-024 |
| BR-015 | 未明確取得相應 content/automation/AI authorization 時，不得 enable 該用途。 | Compliance | FR-008、FR-024 |

## 6. Non-functional Requirements

| ID | Category | Requirement / Threshold | Verification |
|---|---|---|---|
| NFR-001 | Safety | 任一 unknown/stale/error state 不得建立新單；fail-closed coverage 100% critical scenarios | Negative and chaos tests |
| NFR-002 | Idempotency | 相同 Telegram event、Trade Intent 或 client order ID 任意 replay 不產生 duplicate external order | Deterministic replay |
| NFR-003 | Protection | Entry fill 後 5 秒內確認 Protection Order，否則 Emergency Close + emergency pause | Testnet timing test |
| NFR-004 | Auditability | 100% Trade Intents、Risk Decisions、orders、fills、manual commands 有 correlation ID 與 immutable audit chain | Audit replay |
| NFR-005 | Recovery | Service restart 後 5 分鐘內完成 Telegram/Binance/PostgreSQL reconciliation；完成前禁止新單 | Restart test |
| NFR-006 | Security | Secrets 不得存在 Git、logs、reports、fixtures；每 Phase secret scan findings = 0 | Automated scan + manual review |
| NFR-007 | Authorization | Control Bot 未 allowlist request rejection rate = 100%；高風險命令二次確認 | Auth tests |
| NFR-008 | Availability | Production monitoring monthly target 99.5%；execution readiness 以 health state 控制，不以可用率強行開單 | Monitoring reports |
| NFR-009 | Data Retention | Raw content default 7 days；authorization 可設定更短；audit/trade retention 待法律/營運確認 | Retention job tests |
| NFR-010 | Determinism | 相同 fixture + config/schema/model version 必須產生可比較輸出；deterministic path byte-equivalent | Replay tests |
| NFR-011 | Observability | 每個 external stream 有 liveness、source lag、receive lag、last mutation、last full resync 指標 | Monitoring tests |
| NFR-012 | Portability | Local/Testnet/Production 使用相同 container image；只有 externally supplied config/secrets 不同 | Image digest comparison |
| NFR-013 | Testability | 每一 FR 至少對應一個 AC/test；Critical path branch coverage target 100%，overall line coverage target 85% | Coverage/traceability report |
| NFR-014 | Time | Host 時鐘偏差超過 500 ms 時 execution readiness = false | Clock skew injection |

### Back-of-the-envelope Sizing

以下為 Phase 0 assumptions，Phase 2 以實際 channel traffic 更新：

| Item | Assumption | Estimate | Design Impact |
|---|---|---|---|
| Channels | <= 20 active channels | Small single-user workload | 不需要 Kubernetes/broker |
| Messages | <= 2,000/day，burst 10/sec | PostgreSQL easily sufficient | Async collector + bounded workers |
| Text metadata | 10 KB/message average | ~20 MB/day before indexes | PostgreSQL retention manageable |
| Images | 20% messages，1 MB average | ~400 MB/day；7-day ~2.8 GB | Object/file volume + hash dedupe |
| Trade intents | <= 100/day | Low throughput, high correctness | Prefer transaction/idempotency over speed |
| Control users | 1 allowlisted user | Negligible concurrency | Single-user auth model |

## 7. System Architecture

Architecture is defined in [architecture.md](architecture.md). Confirmed boundaries:

- Collector alone holds Telegram User session.
- Orchestrator normalizes/parses and may hold AI key only when authorized.
- Control Bot alone holds Bot Token.
- Execution Gateway alone holds Binance key.
- PostgreSQL stores domain records and transactional outbox; no Redis/RabbitMQ in MVP.
- Runtime external side effects are disabled by phase feature gates and default `PAUSED` control state.

Proposed ADRs are listed under [docs/adr](../adr/) and become Accepted only after Gate 0 user acceptance.

## 8. Data Model

Logical entities, relationships, constraints, states and retention are defined in [logical-data-model.md](logical-data-model.md). Critical invariants:

1. Raw Message is append-only and versioned.
2. Trade Intent has exactly one current Risk Decision.
3. An approved Trade Intent maps to at most one entry client order identity.
4. A nonzero Position must have a confirmed Protection Order or be in Emergency Close.
5. Audit Events cannot be updated or deleted by application roles.
6. Authorization status changes create new records/events; prior permission evidence is retained per policy.

## 9. API and Interface Specification

External contracts and current official source links are in [api-contract-inventory.md](api-contract-inventory.md). Internal interfaces are versioned message contracts, not public Internet APIs in MVP:

| ID | Interface | Producer → Consumer | Auth | Idempotency / Validation |
|---|---|---|---|---|
| API-001 | `RawMessageReceived.v1` | Collector → Orchestrator | Internal service identity | `(channel_id,message_id,edit_version)` |
| API-002 | `NormalizedContentReady.v1` | Orchestrator → Signal Processor | Internal | content hash + schema validation |
| API-003 | `SignalLifecycleChanged.v1` | Signal Processor → Control/Trade Intent Builder | Internal | `signal_id + revision` |
| API-004 | `CandidateDecision.v1` | Control Bot → Orchestrator | allowlisted user + short-lived nonce | candidate/revision/action unique |
| API-005 | `TradeIntentRequested.v1` | Orchestrator → Risk Gate | Internal | globally unique `trade_intent_id` |
| API-006 | `RiskDecisionIssued.v1` | Risk Gate → Execution Gateway | Internal signed envelope | intent/config/account snapshot versions |
| API-007 | `OrderCommand.v1` | Execution workflow → Binance adapter | Execution service only | deterministic client order ID |
| API-008 | `OrderLifecycleChanged.v1` | Execution Gateway → Control/Audit | Internal | Binance order/event identity |
| API-009 | `SystemControlCommand.v1` | Control Bot → Control State | allowlisted user + confirmation | command nonce |

Unknown schema field、unsupported version、missing correlation ID or invalid state transition must be rejected and audited.

## 10. Authorization and Security

- **Authentication:** Telegram user auth、Control Bot numeric user allowlist、Binance signed endpoints、internal service identity。
- **Authorization:** Channel Policy usage gates、phase capability gates、Control command roles、Execution Gateway least privilege。
- **Sensitive Assets:** Telegram session/API hash、Bot Token、AI key、Binance key/secret、database password、private content/media。
- **Secrets:** Runtime secret mounts outside repository；no secret in chat, `.env.example`, fixtures, logs or reports。
- **Audit:** Append-only event with actor、reason、correlation、source/receive times、config/schema version。
- **Abuse Prevention:** Channel allowlist、rate limits、bounded media、command nonce、no arbitrary URL fetch、no dynamic code/prompt tool execution。
- **Compliance:** Telegram Terms/Content Licensing and content owner constraints are enablement gates, not documentation disclaimers。

Detailed controls and verification appear in [threat-model.md](threat-model.md) and [credential-handoff.md](credential-handoff.md).

## 11. Error Handling and Exception Flows

| Scenario | Trigger | System Behavior | User-visible Result | Recovery |
|---|---|---|---|---|
| Telegram authorization lost | Session invalid/frozen/logout | Collector stops; no content processing | P0/P1 alert, channel stale | User reauthorizes locally; history/difference reconciliation |
| Telegram gap | pts/range discontinuity | Mark collector not ready; execute difference/message recovery | Status shows degraded | Resume only after gap closed |
| Unsupported/ambiguous message | Missing evidence/negation conflict | `INCOMPLETE` or manual review | Control Bot explanation | User/channel template update in later revision |
| AI unavailable/unauthorized | No permission, key, timeout or schema failure | ANALYSIS becomes `INSUFFICIENT_DATA`; no trade | Warning, no candidate | Retry only within retention/expiry |
| Market data stale | freshness/liveness threshold exceeded | Risk reject; system can remain monitoring | `MARKET_DATA_STALE` | Full resync before ready |
| Binance request timeout | No definitive response | Query by client order ID before any resend | Pending/reconciliation notice | Reconcile then continue/close |
| Entry fill, protection failure | No confirmed Protection Order within 5 sec | Emergency Close + emergency pause | P0 alert | Manual inspection and explicit resume |
| Daily loss limit | PnL <= -6% baseline | Pause new trades | Daily kill-switch alert | User review; reset at Taipei day boundary per policy |
| Database unavailable | write/read failure | Stop side effects; buffer only bounded in memory if safe | P0 alert | Restore DB, reconcile all external sources |
| Audit write failure | Cannot commit audit in same transaction | Abort state change / external command | P0 alert | Repair storage; no silent continuation |

## 12. Monitoring and Operations

| Item | Metrics | Alert Condition | Runbook Direction |
|---|---|---|---|
| Telegram collector | connected, pts/gap, source lag, receive lag, last event | disconnected > 60 sec or unresolved gap | reconnect, difference recovery, source compare |
| Normalization | queue age, error rate, manual-review rate | queue age > 60 sec or sudden schema errors | pause downstream, inspect fixtures/version |
| Market data | socket liveness, last update, resync age, clock skew | any hard freshness threshold exceeded | mark execution not ready, full resync |
| Execution | order latency, ambiguous outcomes, protection latency | protection > 5 sec or unknown order state | emergency close/pause/reconcile |
| Risk | rejects by reason, positions, margin, daily PnL | limit reached or snapshot stale | pause and notify |
| Database | availability, disk, replication/backup status | unavailable or disk > 80% | stop side effects, restore/expand |
| Security | auth failures, secret scan, unexpected outbound domain | any secret finding or repeated unauthorized commands | revoke/rotate, isolate, investigate |
| Gate governance | phase state, evidence completeness | phase N+1 enabled without acceptance | block startup and report Critical |

### Deployment and Environments

- `offline`: no external credentials/network side effects.
- `telegram-readonly`: Telegram credential only; no AI/Binance.
- `analysis-test`: authorized/synthetic AI + Binance public data only.
- `binance-testnet`: Testnet credential only; hardcoded allowed hosts.
- `production-canary`: production key on fixed IP subaccount; starts paused.

### Backup and Recovery

- PostgreSQL daily encrypted backup and pre-deploy snapshot; retention TBD before Phase 8.
- Raw media volume backup policy follows authorization/retention; no indefinite content archive.
- Restore drill required in Gate 7.
- After restore, all external positions/orders and Telegram ranges must reconcile before ready.

## 13. Acceptance Criteria

| ID | Acceptance Item | Expected Result | Related Requirements |
|---|---|---|---|
| AC-001 | Gate enforcement | Phase N+1 capability cannot activate without `READY + USER_ACCEPTED` | FR-001 |
| AC-002 | Channel allowlist | Unknown/disabled channel produces no downstream action | FR-002、FR-008 |
| AC-003 | Telegram completeness | New/edit/reply/media/history/restart evidence matches source | FR-003–FR-006 |
| AC-004 | Deterministic normalization | Same fixtures generate identical normalized output | FR-007 |
| AC-005 | Authorization gate | Unapproved access/automation/AI paths are blocked and audited | FR-008 |
| AC-006 | Signal safety | Negation/cancel do not open; missing symbol/side incomplete; missing SL default only | FR-009、FR-010 |
| AC-007 | Follow-up safety | Ambiguous update never changes a position automatically | FR-011 |
| AC-008 | AI boundary | No evidence means no invented field; LLM cannot call execution or calculate size | FR-012、FR-013 |
| AC-009 | Human approval | ANALYSIS candidate requires valid allowlisted approval before intent | FR-014、FR-015 |
| AC-010 | Risk hard limits | 3%/3 positions/-6%/10%/30%/conflict/freshness enforced | FR-016 |
| AC-011 | Testnet execution | Correct filters, One-way, Isolated 5x and environment isolation | FR-017、FR-023 |
| AC-012 | Order idempotency | Timeout/replay creates no duplicate Binance entry | FR-018 |
| AC-013 | Protection | Fill receives confirmed protection <=5 sec or emergency close/pause | FR-019 |
| AC-014 | Reconciliation | Restart restores exact external/local lifecycle state | FR-020 |
| AC-015 | Pause semantics | New orders blocked while monitoring and safe close remain available | FR-021 |
| AC-016 | Audit replay | Complete lifecycle reconstructable from immutable events | FR-022 |
| AC-017 | Production canary | Starts paused, one accepted channel only, restricted subaccount key | FR-024、FR-025 |

Full requirement-to-test mapping is maintained in [acceptance-traceability.md](acceptance-traceability.md).

## 14. Implementation Plan

| Phase | Deliverable | Dependency | Exit Gate |
|---|---|---|---|
| 0 | Requirements、architecture、data、security、test contracts | User decisions and official docs | Gate 0 User Accepted |
| 1 | Offline foundation | Accepted Phase 0 | Offline tests + Gate 1 acceptance |
| 2 | Telegram read-only collector | Accepted Phase 1 + local Telegram credential | 24h read-only Gate 2 |
| 3 | Channel registry/normalization | Accepted Phase 2 + channel samples | Fixture/authorization Gate 3 |
| 4 | Signal lifecycle/control bot | Accepted Phase 3 + Bot Token | Parser/auth Gate 4 |
| 5 | AI analysis/market confirmation | Accepted Phase 4 + valid authorization/synthetic data | Schema/freshness Gate 5 |
| 6 | Risk/Binance Testnet | Accepted Phase 5 + Testnet credential | Golden/Testnet Gate 6 |
| 7 | End-to-end Testnet | Accepted Phase 6 | Full matrix + 24h soak Gate 7 |
| 8 | Production Canary | Accepted Phase 7 + fixed IP/subaccount key | Single-channel Gate 8 |

## 15. Risks and Open Questions

### Risks

| Risk | Impact | Likelihood | Mitigation | Owner |
|---|---|---|---|---|
| Telegram/API terms prohibit intended processing | Phase 3/5/8 blocked | High until permission reviewed | Per-channel authorization records; legal/platform review; fail closed | User / Legal TBD |
| Free-form signal misparsed | Wrong trade | Medium/High | Evidence spans, deterministic parser, negative fixtures, manual fallback | Technical/QA TBD |
| Protection order contract drift | Unprotected position | Medium | Phase 6 contract spike, Testnet, 5-sec emergency close | Technical TBD |
| Aggressive 3% single-trade risk | Rapid drawdown | High | Low-balance subaccount, 3-position/6% daily hard limits | User |
| Signal format drift | Parser recall/precision degradation | High | Versioned per-channel fixtures and enablement Gate | User/QA TBD |
| Duplicate/ambiguous API result | Duplicate order | Medium | client order identity + query-before-resend + reconciliation | Technical TBD |
| Credential exposure | Account/content/fund compromise | Medium | isolated mounts, least privilege, scan, rotate, fixed IP | Security TBD |
| No multi-week shadow period | Unseen production behavior | High | Full Testnet matrix + 24h technical soak + one-channel low-fund canary | User |

### Open Questions / TBD

| ID | Question | Impact | Owner | Blocking Gate |
|---|---|---|---|---|
| TBD-001 | Actual Source Channel list、type、symbol allowlist、至少一則匿名化代表訊息；Gate 3 前擴充至每頻道 20 fixtures | Phase 0 scope、parser and onboarding | User | Gate 0 / Gate 3 |
| TBD-002 | Per-channel access/automation/AI processing authorization status；Gate 0 可明確填 `UNKNOWN/PENDING`，但不可留白 | Compliance enablement | User / Legal | Gate 0 status / Gate 3/5/8 enablement |
| TBD-003 | Exact Position ROE denominator、fees/funding/slippage buffer and stop conversion | Risk math | Technical + User | Gate 6 |
| TBD-004 | `ANALYSIS` Strategy Contract rules per channel | Market confirmation | User + Product/Technical | Gate 5 |
| TBD-005 | AI provider/model/schema version and data retention terms | AI adapter | User + Technical | Gate 5 |
| TBD-006 | VPS provider/region/fixed IP and secret store | Deployment/security | User + Ops | Gate 8 |
| TBD-007 | Production low-balance subaccount maximum USDT | Blast radius | User | Gate 8 |
| TBD-008 | Raw/audit/trade retention beyond Phase 7 and applicable legal obligations | Data lifecycle | User / Legal | Gate 8 |
| TBD-009 | Owners for Technical、QA、Security、Ops、Legal roles | Handoff/accountability | User | Before affected Gate |

TBD-001 與 TBD-002 是使用者原始 Phase 0 Required Inputs，目前阻擋 Gate 0。其餘 TBD 已指定在對應後續 Gate 前成為 blocking。

## 16. Traceability

All `FR-001`–`FR-025` and `NFR-001`–`NFR-014` are mapped in [acceptance-traceability.md](acceptance-traceability.md). Gate 0 validates requirement completeness, not runtime behavior.

## 17. Handoff Notes

- **Deliverable:** Phase 0 integrated specification package
- **Specification Version / Date:** v0.1 / 2026-09-02
- **Confirmed Decisions:** Sequential Gates；ANALYSIS manual approval；EXECUTION_SIGNAL automated only after Production Gate；Binance USDⓈ-M One-way Isolated 5x；3%/3 positions/-6% risk；default -30% position ROE stop；Control Bot；Docker VPS；separate credentials
- **Assumptions:** <=20 channels、<=2,000 messages/day、PostgreSQL outbox sufficient
- **In Scope:** Phase 0 documents only
- **Out of Scope:** Runtime implementation and all credentials
- **Open Questions:** TBD-001–TBD-009
- **Risks / Dependencies:** Telegram content terms、signal ambiguity、aggressive risk、contract drift
- **Next Owner:** User / Acceptance Owner
- **Verification Required:** 先依 [Channel Onboarding Template](channel-onboarding-template.md) 提供每頻道最小非機密資料，再 re-review [Gate 0 Checklist](gate-0-checklist.md)；Gate 0 尚不可接受

## References

- [Telegram User Authorization](https://core.telegram.org/api/auth)
- [Telegram Working with Updates](https://core.telegram.org/api/updates)
- [Telegram messages.getHistory](https://core.telegram.org/method/messages.getHistory)
- [Telegram API Terms](https://core.telegram.org/api/terms)
- [Telegram Content Licensing](https://telegram.org/tos/content-licensing)
- [Binance USDⓈ-M Futures General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)
- [Binance USDⓈ-M Futures Trade API](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order)
- [Binance USDⓈ-M Futures Exchange Information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
- [Binance USDⓈ-M User Data Stream](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-api/user-data-streams)
