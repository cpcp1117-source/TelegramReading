# ADR-0003: Keep Trade Authority Deterministic and Outside the LLM

- **Status:** Accepted
- **Date:** 2026-09-02
- **Owners:** User（Risk Owner）、Technical Owner（待確認）

## Context

AI 適合抽取非結構化 analysis thesis，但可能產生 schema 漂移、遺漏否定詞或缺乏來源證據。若 AI 可直接計算 quantity、修改風控或呼叫 Binance，一次 hallucination 即可能成為真實交易事故。

## Decision

LLM 只能輸出 schema-valid、含 evidence spans 的 Thesis 或 Normalized Signal 建議，不能接觸 Binance credential、不能計算 quantity、不能改寫 Risk Rules，也不能直接建立 Order。所有 Trade Intent 必須通過 deterministic validation、freshness、conflict 與 Risk Gate；ANALYSIS 路徑另外需要人工核准。

## Alternatives Considered

| Alternative | Benefits | Costs / Risks | Reason Not Selected |
|---|---|---|---|
| LLM 直接工具下單 | 彈性高、開發快 | 不可重現、權限與 hallucination 風險高 | 不符合可稽核與 fail-closed 原則 |
| 完全不使用 AI | 行為最可預測 | 無法有效處理非結構化 analysis 內容 | 不符合分析頻道需求 |
| AI 抽取 + deterministic authority | 兼顧語意能力與可驗證執行 | 需維護 schema 與 validators | Selected |

## Consequences

- AI provider 不可成為 execution dependency；不可用時只影響 ANALYSIS extraction。
- 所有 AI output 必須保存 model、prompt/schema version、evidence 與 validation result。
- 無證據、低 confidence、未知欄位或 stale market data 一律轉 `WAIT/INCOMPLETE`。
- Risk Engine 與 Execution Gateway 必須能以 fixtures 完全離線測試。

## Reversal or Supersession Trigger

即使未來模型可靠度提升，也只能在獨立安全審查、長期 evidence 與新 ADR 後考慮擴權；不得以 prompt 更新直接改變 trade authority。

## References

- [System Specification](../phase-0/system-spec.md)
- [Threat Model](../phase-0/threat-model.md)
