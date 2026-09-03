# Test Strategy

- **Version:** v0.1
- **Goal:** Prove each Phase's newly introduced behavior before User Acceptance; tests from earlier Phases do not substitute for coverage of new capability.

## 1. Test Principles

1. One active Phase only; no test may require a future-Phase credential or implementation.
2. All external behavior is wrapped by adapters and testable with deterministic fixtures.
3. Critical safety tests are negative-first: stale, ambiguous, duplicated, unauthorized, disconnected and partial states.
4. Every test result records command, environment, version/commit, start/end time, pass/fail count and redacted evidence path.
5. A failed Critical/Major test makes Gate Verdict `NOT_READY` until remediation and full affected-suite rerun.
6. Mock/Testnet evidence must never be described as Production or profitability evidence.

## 2. Test Levels

| Level | Purpose | Data | Side Effects |
|---|---|---|---|
| Static | Format、typing、lint、schema、secret scan、dependency checks | Source/docs/config | None |
| Unit | Pure normalization、state transitions、risk math、IDs | Synthetic fixtures | None |
| Contract | Adapter behavior against recorded/official schemas | Sanitized responses or provider sandbox | None/Testnet only |
| Integration | PostgreSQL/outbox、service boundaries、restart | Synthetic fixtures | Local only |
| End-to-end | Telegram test channel → Binance Testnet → Control Bot | Controlled test content | Testnet only |
| Chaos/Recovery | Disconnect、timeout、partial fill、restore | Synthetic/Testnet | Testnet only |
| Security | Authorization、secret leak、prompt injection、network allowlist | Synthetic/adversarial fixtures | None/Testnet |
| Operational Soak | Liveness、lag、resource growth、reconnect | Read-only/Testnet | No real funds until Gate 8 |

## 3. Phase Test Matrix

### Gate 0 — Specification

| Test ID | Check | Pass Criterion |
|---|---|---|
| P0-T01 | Deliverable inventory | All required Phase 0 files exist and links resolve locally |
| P0-T02 | Requirement IDs | FR/NFR/AC IDs unique and complete |
| P0-T03 | Traceability | Every FR/NFR maps to AC and blocking Phase/test |
| P0-T04 | Decision consistency | Risk, execution, authorization and phase rules do not contradict across docs |
| P0-T05 | Open questions | Every unresolved material item has owner and blocking Gate |
| P0-T06 | Security scan | No credential-like value or session/token in files |
| P0-T07 | Scope check | No runtime code, migration, Docker or external credential exists |

### Gate 1 — Offline Foundation

| Area | Required Tests |
|---|---|
| Project quality | formatter check、lint、type check、unit tests、coverage |
| Database | migration upgrade/rollback/upgrade、constraints、append-only enforcement |
| Outbox | crash before/after commit、duplicate consumer、checkpoint recovery |
| Simulator | deterministic event replay、edit/reply/duplicate fixtures |
| Containers | clean build/start/health/restart、same image digest by environment |
| Security | secret scan、image/dependency scan、no external egress required |

### Gate 2 — Telegram Read-only

- NewMessage、EditedMessage、reply/forward、caption、image reference.
- History pagination and controlled update gap recovery.
- Duplicate delivery from update/history is idempotent.
- Controlled restart and session re-use.
- 24-hour read-only soak with source message-ID comparison.
- Session/API hash absent from Git/log/report.
- No parser、AI、Binance modules activated.

### Gate 3 — Registry and Normalization

- At least 20 fixtures per channel using the onboarding template.
- Numeric channel allowlist and status/authorization paths.
- Text/caption/symbol alias deterministic normalization.
- Media hash/type/size and low-confidence OCR manual review.
- Unknown channel/symbol、stale、revoked authorization fail closed.
- Same fixture/config digest yields identical output.

### Gate 4 — Signal Lifecycle and Control Bot

- Clear long/short; missing symbol/side; authored/missing/invalid SL; missing TP.
- Negation, cancel, edit, supersede and expiry.
- Reply/signal-ID link and ambiguous same-symbol follow-up.
- Duplicate lifecycle cannot create two intents.
- Bot numeric ID allowlist, command nonce, expired approval, double confirmation.
- All order previews remain mock; network tests prove no Binance signed endpoint access.

### Gate 5 — AI and Market Confirmation

- Authorized/synthetic input only; unauthorized content blocked before provider call.
- Structured schema: unknown field, missing evidence, invented symbol/price rejected.
- Prompt injection/adversarial channel text cannot access tools or secrets.
- Fixed model/schema/config version replay and diff.
- Market liveness/source/receive/resync age and 500 ms clock skew.
- `WAIT/CONFIRMED/INSUFFICIENT_DATA/INVALIDATED` Strategy Contract cases.
- ANALYSIS confirmed candidate still requires valid human approval.

### Gate 6 — Risk and Binance Testnet

Golden tests must cover LONG/SHORT, decimal rounding, valid/invalid authored SL, default ROE stop, fees/funding/slippage assumptions and quantity caps. Negative tests cover:

- risk >3%; initial margin >10%; total margin >30%; fourth position;
- existing manual position; one-way reverse conflict; daily loss <=-6%;
- stale market/account/config; wrong environment/host; server clock error;
- API timeout/query-before-resend; partial fill; cancel; reject;
- Protection Order success/failure/timeout; Emergency Close ambiguity;
- User Data Stream disconnect/expiry and REST reconciliation.

### Gate 7 — End-to-end Testnet

Use a controlled Telegram test channel and the full matrix from the accepted plan. Required zero-tolerance outcomes:

- duplicate external entry = 0;
- wrong symbol/side = 0;
- known unprotected nonzero position outside Emergency Close = 0;
- lost lifecycle/audit event = 0;
- unresolved mismatch while execution readiness is true = 0.

Complete a 24-hour technical soak, database backup/restore, service restart and external reconciliation.

### Gate 8 — Production Canary

- Production starts paused; read-only account/permission/mode/host preflight.
- Separate low-balance subaccount key, withdrawal disabled, fixed IP.
- Controlled minimum-size entry/protection/close under explicit user observation.
- One accepted EXECUTION_SIGNAL channel only.
- Restart/reconciliation and kill switch verified.
- Each additional channel is a separate `8.n` Gate.

## 4. Coverage and Quality Thresholds

| Metric | Threshold |
|---|---:|
| Critical safety scenario coverage | 100% listed scenarios |
| Overall line coverage (Phase 1+) | >=85% |
| Critical modules branch coverage | 100% targeted branches |
| Parser false auto-open on accepted fixtures | 0 |
| Duplicate external order in replay/chaos | 0 |
| Secret scan findings | 0 |
| Critical/Major open issues at Gate | 0 |

Coverage percentage alone never proves acceptance; all named scenarios and external contract tests remain mandatory.

## 5. Test Evidence Format

Each Phase Report includes:

```text
Phase:
Commit / image digest:
Environment:
Command:
Started / finished (UTC):
Passed / failed / skipped:
Coverage:
Evidence files:
Credential disclosure scan:
Known issues:
Verdict: READY / NOT_READY
User acceptance: PENDING / ACCEPTED / REJECTED
```

No skipped Critical test is acceptable. A provider-dependent test may be `BLOCKED`, which makes the Gate `NOT_READY` rather than passed.

## 6. Regression Policy

- Any fix reruns the failing test, its parent suite and all affected downstream accepted suites.
- External API contract or dependency version change reruns contract tests before deployment.
- Channel Policy/fixture change reruns that Channel Gate and any impacted signal/strategy tests.
- Risk Rule change requires new specification revision, golden-test update and user acceptance before activation.
- Production incident demotes runtime to `PAUSED` and reopens the earliest affected Gate.
