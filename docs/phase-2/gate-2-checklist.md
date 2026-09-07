# Gate 2 Checklist

- **Phase:** Phase 2 — Telegram Read-only Collector
- **Fixed Point:** `f4ccff6a039f42df35fc81054c9208861b42ecd3`
- **Gate Verdict:** `NOT READY — 5 Acceptance Criteria PENDING/PARTIAL` (see table below; nothing below is marked PASS that did not actually happen)
- **User Acceptance:** `RISK-ACCEPTED 2026-09-06` (explicit override, not a claim that Gate 2 evidence is complete — see User Risk Acceptance Record below)
- **Phase 3 Authorization:** `GRANTED WITH OPEN RISK 2026-09-06`
- **Governance Note:** This authorization is a deliberate deviation from [ADR-0001 Sequential Stage Gates](../adr/0001-sequential-stage-gates.md), which states Phase N+1 code/credential integration requires the prior Gate to be `READY` and `USER_ACCEPTED`, and that only a new ADR may permit otherwise. No such ADR has been written. The user was shown this conflict and chose to proceed anyway; it is recorded here rather than silently overridden.

| Gate Condition (from [phase-kickoff.md](phase-kickoff.md)) | Result | Evidence |
|---|---|---|
| 連續 24 小時 read-only 運行證據 | **PENDING** | longest run so far ~21 minutes; see [test-evidence.md](test-evidence.md) §2 |
| 對 `@followgerry` 人工比對，訊息 ID 與數量一致 | **PARTIAL** | 20/500 rows spot-checked and correct; full reconciliation pending |
| controlled restart 後無漏訊、無重複 | **PARTIAL** | no duplicates confirmed after restart; no-loss not provable yet (no messages arrived during the gap) |
| 編輯訊息保留原版本與新版本 | **PENDING** | no live edit observed; unit-level coverage only |
| 僅讀取 Collector Account 已合法加入或可合法存取的內容 | PASS | `dialogs` allowlist check; single `channel_id` in DB |
| Telegram session 不出現在 log、Git 或測試報告 | PASS | [security-check.md](security-check.md) |
| Phase Report、Test Evidence、Requirement Traceability、Security Check、Known Issues、Gate Verdict | PASS (this package) | this document and its siblings in `docs/phase-2/` |
| Critical = 0、Major = 0，且所有 Phase 2 Acceptance Criteria 通過 | **NOT MET** | Critical/Major = 0, but 4 acceptance criteria remain PENDING/PARTIAL (see [requirement-traceability.md](requirement-traceability.md)) |
| 使用者明確驗收前，不得開始 Phase 3 | enforced | no acceptance statement recorded |

## Remaining Work Before Gate 2 Can Be Evaluated

1. Run the collector continuously for 24 hours without an unhandled crash (soak).
2. During or after the soak, perform another controlled restart while messages are actively arriving, to prove no message loss (not just no duplication).
3. Capture at least one live edited message and confirm both `edit_version=0` and `edit_version>=1` rows persist with correct content.
4. Complete a full manual reconciliation of all collected rows (not just a 20-row sample) against `@followgerry`.
5. Re-run the combined unit + integration test suite against this fixed point and confirm ≥85% coverage.
6. Only after 1–5 pass, update this checklist's Gate Verdict and record an explicit user acceptance statement (per the Phase 1 pattern in [gate-1-checklist.md](../phase-1/gate-1-checklist.md)).

## User Risk Acceptance Record

The user was shown, in chat on 2026-09-06, that the 5 unresolved items above (24-hour soak, controlled-restart no-loss, live edit-version retention, full 500-row reconciliation, combined test coverage) were not actually completed, and was asked to choose between (a) completing real verification before Phase 3, (b) proceeding with the gaps explicitly documented as accepted risk, or (c) discussing which gaps matter most first.

The user chose option (b): 「跳過驗證，但明確記錄為風險接受」(skip verification, but explicitly record it as risk acceptance) — after an initial request to mark the evidence "as PASS," which was declined because it would misrepresent work that had not happened.

This is recorded as an explicit, informed **risk acceptance**, not as evidence that Gate 2's acceptance criteria were met. The 5 items remain open and are carried forward as inherited risk into Phase 3 — see `docs/phase-3/phase-kickoff.md` once created.

This acceptance also constitutes a documented deviation from [ADR-0001](../adr/0001-sequential-stage-gates.md), which the user was informed of before making this choice.
