# ADR-0001: Enforce Sequential Stage Gates

- **Status:** Accepted
- **Date:** 2026-09-02
- **Owners:** User（Acceptance Owner）、Technical Owner（待確認）

## Context

系統最終可對真實 USDⓈ-M Futures 帳戶送單。若 Telegram ingestion、訊號解析、風控與交易介面並行開發，測試證據將難以隔離，且早期錯誤可能在後續階段被誤認為已驗證能力。

## Decision

Phase 0–8 嚴格循序執行。每一 Phase 必須產出 Phase Report、Test Evidence、Traceability、Security Check、Known Issues 與 Gate Verdict。只有 `READY` 且使用者明確 `USER_ACCEPTED` 後，才能建立下一 Phase 的程式碼或 credential integration。

## Alternatives Considered

| Alternative | Benefits | Costs / Risks | Reason Not Selected |
|---|---|---|---|
| 多 Phase 並行 | 可能縮短日曆工期 | 問題來源、測試範圍與授權邊界混淆 | 不符合使用者明確要求 |
| 先做完整 prototype 再補文件 | 能較快看到 UI | 交易安全與合規 Gate 太晚發現 | 不適合真實資金系統 |
| Sequential Stage Gate | 可追溯、可停損、責任清楚 | 每一 Gate 都需要等待驗收 | Selected |

## Consequences

- Gate 未通過時，後續程式、credential 與 external integration 禁止提前建立。
- 介面可以在規格中預先定義，但不得提前實作。
- 每次驗收形成可稽核的 acceptance record。
- 修正必須留在原 Phase，重跑完整受影響測試。

## Reversal or Supersession Trigger

只有使用者明確修改開發治理方式，並另立 ADR，才能允許 Phase 並行。

## References

- [System Specification](../phase-0/system-spec.md)
- [Gate 0 Checklist](../phase-0/gate-0-checklist.md)
