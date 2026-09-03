# ADR-0002: Separate Telegram, AI, Control, and Execution Credential Boundaries

- **Status:** Proposed
- **Date:** 2026-09-02
- **Owners:** Security Owner（待確認）、Technical Owner（待確認）

## Context

Telegram User session 可代表使用者讀取其帳號可見內容；Control Bot Token 可執行操作介面；AI key 可將內容傳送給模型供應商；Binance key 最終可交易。若單一 process 同時持有所有 secrets，任一模組漏洞都會擴大成全系統權限暴露。

## Decision

部署時切分為 Collector、Orchestrator、Control Bot、Execution Gateway 四個 credential boundaries。只有 Collector 可讀 Telegram User session；只有 Orchestrator 可讀 AI key；只有 Control Bot 可讀 Bot Token；只有 Execution Gateway 可讀 Binance key。它們以版本化內部 contract 與 PostgreSQL transactional outbox 交換非 secret 資料。

## Alternatives Considered

| Alternative | Benefits | Costs / Risks | Reason Not Selected |
|---|---|---|---|
| 單一 process 共享 `.env` | 開發簡單 | 任一程式路徑可接觸全部 secrets | Blast radius 過大 |
| 外部 message broker + 多服務 | 強隔離、擴展性高 | MVP 維運元件增加 | 暫不需要額外 broker |
| 分離服務 + PostgreSQL outbox | 明確最小權限、元件較少 | 需設計 outbox/reconciliation | Selected |

## Consequences

- Phase 1 即使建立 offline containers，也不得放入真實 secret。
- 每個 Phase 只引入當期必要 credential。
- Execution Gateway 不接收原始 Telegram 內容或 LLM prompt。
- 需測試跨 process idempotency、outbox replay 與 credential mount 權限。

## Reversal or Supersession Trigger

若部署平台無法提供獨立 secret mount，或測得 PostgreSQL outbox 無法滿足可靠性需求，需以新 ADR 重評 secrets manager 或 broker。

## References

- [Architecture](../phase-0/architecture.md)
- [Credential Handoff](../phase-0/credential-handoff.md)
- [Threat Model](../phase-0/threat-model.md)
