# Acceptance Traceability Matrix

- **Version:** v0.1
- **Rule:** Every formal requirement maps to acceptance evidence and a blocking Phase. Gate 0 validates the specification mapping; runtime evidence is produced only in the listed later Gate.

## 1. Functional Requirements

| Requirement | Acceptance Criteria | Component / Contract | Planned Test / Evidence | Blocking Gate |
|---|---|---|---|---:|
| FR-001 Phase governance | AC-001 | Phase Gate / startup capability | P0-T01/P0-T03; future capability-enforcement test | 0/each |
| FR-002 Channel Policy | AC-002 | Channel Registry | Unknown/disabled channel；static/dynamic symbol scope；missing/ambiguous/ineligible/stale mapping tests | 3 |
| FR-003 Telegram events | AC-003 | Collector | New/edit/reply/forward/caption/image source comparison | 2 |
| FR-004 Telegram gap recovery | AC-003 | Collector checkpoint/difference adapter | Controlled disconnect/gap/recovery trace | 2 |
| FR-005 Message versions/dedupe | AC-003 | Raw Message | Update+history duplicate, edit-version tests | 2 |
| FR-006 Media hash/metadata | AC-003 | Media Object | Same-media dedupe, type/size/hash tests | 2/3 |
| FR-007 Normalization/evidence | AC-004 | Normalizer | Deterministic fixture replay | 3 |
| FR-008 Authorization gates | AC-002、AC-005 | Policy/Authorization | Access/automation/AI status matrix | 3/5/8 |
| FR-009 Signal parser | AC-006 | EXECUTION parser | Per-channel fixture matrix | 4 |
| FR-010 Missing fields/default stop | AC-006 | Signal validator | Missing symbol/side/SL/TP, wrong-side SL | 4 |
| FR-011 Follow-up linking | AC-007 | Signal Link | reply/ID/unique/ambiguous link tests | 4 |
| FR-012 Structured Thesis | AC-008 | AI adapter/Thesis validator | schema/evidence/invention/prompt injection tests | 5 |
| FR-013 Strategy/market status | AC-008 | Strategy Contract/Market Monitor | fresh/stale/wait/confirmed/invalidated replay | 5 |
| FR-014 Candidate approval | AC-009 | Candidate Trade | authorized/rejected/expired/revision tests | 4/5 |
| FR-015 Control Bot commands | AC-009、AC-015 | Bot command adapter | allowlist/nonce/double-confirmation tests | 4/7 |
| FR-016 Risk hard limits | AC-010 | Risk Engine | golden and negative risk matrix | 6 |
| FR-017 Binance mode/filters | AC-011 | Binance adapter | exchangeInfo、One-way、Isolated 5x preflight | 6 |
| FR-018 Order idempotency | AC-012 | Execution workflow | timeout/query-before-resend/replay tests | 6/7 |
| FR-019 Protection/Emergency Close | AC-013 | Protection workflow | <=5 sec success and failure/ambiguous close | 6/7 |
| FR-020 Reconciliation | AC-014 | User Stream/REST reconciler | restart/stream expiry/external diff | 6/7/8 |
| FR-021 Pause semantics | AC-015 | System Control State | new-order block + safe close availability | 4/6/7 |
| FR-022 Audit replay | AC-016 | Audit Event/Outbox | lifecycle reconstruction and crash consistency | 1/7 |
| FR-023 Environment isolation | AC-011 | Config/Binance adapter | host/credential namespace and no-fallback tests | 6/7 |
| FR-024 Production canary | AC-017 | Deployment/preflight | paused start、restricted key、one channel | 8 |
| FR-025 Per-channel production Gate | AC-017 | Channel/Gate state | enable denied without Channel Gate acceptance | 8.n |

## 2. Non-functional Requirements

| Requirement | Related AC | Planned Evidence | Blocking Gate |
|---|---|---|---:|
| NFR-001 Fail closed | AC-002、AC-005、AC-008、AC-010 | Named negative/chaos scenarios all reject | 3–7 |
| NFR-002 Idempotency | AC-003、AC-012 | Message/event/intent/order replay | 1/2/6/7 |
| NFR-003 Protection <=5 sec | AC-013 | Testnet timestamped trace | 6/7 |
| NFR-004 Complete audit | AC-016 | Correlation coverage and lifecycle replay | 1/7 |
| NFR-005 Recovery <=5 min | AC-014 | Restart timer + readiness false until reconciled | 7 |
| NFR-006 Secrets findings = 0 | AC-017 | Automated/manual secret/report scan each Gate | Every Gate |
| NFR-007 Bot authorization | AC-009、AC-015 | Unauthorized actor rejection 100% | 4/7 |
| NFR-008 99.5% monitoring target | AC-014 | Production metrics report; not trade-readiness override | 8 |
| NFR-009 Retention | AC-005、AC-016 | Expiry/deletion/audit retention jobs | 3/5/8 |
| NFR-010 Determinism | AC-004、AC-008 | Fixture/config/model-version replay diff | 3/5 |
| NFR-011 Observability | AC-003、AC-014 | liveness/lag/resync metrics and alerts | 2/5/7 |
| NFR-012 Same image portability | AC-011、AC-017 | Image digest comparison and env-only config | 1/8 |
| NFR-013 Test coverage | All | >=85% line, critical targeted branches/scenarios 100% | 1+ |
| NFR-014 Clock skew <=500 ms | AC-010、AC-011 | Skew injection sets execution not ready | 6 |

## 3. Business Rule Traceability

| Business Rule | Test Focus | Gate |
|---|---|---:|
| BR-001 Fixed Channel type | Policy version cannot switch runtime meaning | 3 |
| BR-002 ANALYSIS manual vs EXECUTION automation | Candidate approval and phase capability | 4/5/8 |
| BR-003 Image manual only | OCR path cannot create automatic intent | 3/4 |
| BR-004 SL precedence/default/reject | Authored/missing/wrong-side cases | 4/6 |
| BR-005 No synthesized TP | Missing TP fixture | 4 |
| BR-006 60s/10s/50bps freshness | Boundary and stale tests | 4/5/6 |
| BR-007 No auto reverse | Existing opposite position conflict | 6 |
| BR-008 One-way/Isolated 5x | Testnet preflight | 6 |
| BR-009 3%/10%/30% | Risk golden/limit tests | 6 |
| BR-010 Max 3 positions | Manual + system positions count | 6 |
| BR-011 Daily -6% Taipei | Boundary/baseline/reset tests | 6 |
| BR-012 Fail closed on dependency health | Disconnect/stale/unknown chaos | 5–7 |
| BR-013 LLM no trade authority | Dependency/import/tool/credential isolation | 5 |
| BR-014 Production key restrictions | Permission/IP/subaccount evidence | 8 |
| BR-015 Authorization before enablement | Scope matrix and revoked status | 3/5/8 |

## 4. Gate 0 Deliverable Traceability

| Required Deliverable | Artifact | Verification |
|---|---|---|
| System Specification | `system-spec.md` | P0-T02–P0-T05 |
| Architecture and Data Flow | `architecture.md` | Diagram fences、component/sequence/phase review |
| Logical Data Model | `logical-data-model.md` | Entity/invariant/lifecycle consistency review |
| Channel Onboarding Template | `channel-onboarding-template.md` | Required identity/authorization/sample fields review |
| Credential Handoff Procedure | `credential-handoff.md` | Secret boundary and phase introduction review |
| Threat Model | `threat-model.md` | Assets、threats、controls、verification Gate mapping |
| Test Strategy | `test-strategy.md` | Every Phase, evidence format, thresholds |
| Acceptance Traceability Matrix | This file | FR/NFR/BR ID coverage script/manual check |
| API Contract Inventory | `api-contract-inventory.md` | Official links and contract-spike mapping |
| Phase Report | `phase-report.md` | Completed/remaining/evidence/issues/verdict |
| Test Evidence | `validation-evidence.txt` | Recorded commands/scopes, machine result and failure record |
| Delivery Quality Review | `quality-review.md` | Critical/Major/Minor findings and independent Gate verdict |
| Gate 0 Checklist | `gate-0-checklist.md` | User acceptance boundary |
| Domain Context / ADRs | `CONTEXT.md`, `../adr/*.md` | Term/decision consistency |
