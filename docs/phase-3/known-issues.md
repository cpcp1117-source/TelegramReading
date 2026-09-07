# Phase 3 Known Issues

## Open Product Findings

| Severity | Count | Items |
|---|---:|---|
| Critical | 0 | None |
| Major | 4 | See below |
| Minor | 2 | See below |

No defect was found in the `channel_policies` schema, enforcement logic, or the multi-topic collector during this phase's testing. The items below are onboarding/completeness gaps and intentionally-deferred scope, not bugs in what was built.

## Major

| ID | Item | Why it matters | Owner |
|---|---|---|---|
| P3-MAJOR-001 | `@followgerry` has only 1 of the required 20 fixtures ([monster-currency-universe.md](../phase-0/channels/monster-currency-universe.md) §6: "Gate 3 fixture set: NOT STARTED") | Gate 3 requires 20 representative fixtures per channel; this is the only channel-level requirement not met by either onboarded source | User |
| P3-MAJOR-002 | No automated retention/deletion enforcement | `channel_policies.raw_retention_days=7` is declared for both channels but nothing in the codebase deletes data past that age — raw messages accumulate indefinitely today | Technical (future phase) |
| P3-MAJOR-003 | No live/dynamic policy reload | A `gate_decision` change to `PAUSED`/`REJECTED`, or an authorization revocation, only takes effect on the collector's next restart — a running session keeps collecting under the policy state read at its own startup | Technical (future phase) |
| P3-MAJOR-004 | AI-processing / media-storage authorization not enforced by any code | `ai_authorization`/`media_authorization` columns are captured and seeded `GRANTED` but nothing consumes them yet (no Phase 5 code exists) — flagging now so Phase 5 doesn't skip checking them when that code is written | Technical (Phase 5) |

## Minor

| ID | Item | Note |
|---|---|---|
| P3-MINOR-001 | Only 2 channels total registered | The Channel Policy mechanism itself is not stress-tested at a scale beyond 2 — reasonable for now per the user's explicit choice to use only these two channels |
| P3-MINOR-002 | Symbol-scope enforcement (`STATIC_ALLOWLIST`/`BINANCE_USDM_ACTIVE_PERPETUAL`) is captured but not enforced by any parser | Expected — no parser exists until Phase 4 |

## Inherited From Phase 2 (still open)

Per [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md), the following remain unresolved and now apply across both onboarded channels, not just one: 24-hour continuous read-only run, controlled-restart no-loss proof, live edited-message version retention, full manual reconciliation beyond a sample, and combined unit+integration coverage re-verification at ≥85%.

## Non-blocking Notices

| ID | Notice | Gate Impact |
|---|---|---|
| NOTICE-001 | The collector Docker image did not rebuild automatically before this phase's first `collect` run, so the container briefly ran a stale, pre-multi-topic image and migration 0004 didn't apply. Fixed by forcing a rebuild (`docker compose --profile telegram build collector`) before migration/collect in `telegram-bootstrap.ps1`. Root-caused and corrected within this phase; no lasting effect (verified via direct database query after the fix). | None |
