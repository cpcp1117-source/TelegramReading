# Phase 2 Known Issues

## Open Product Findings

| Severity | Count | Items |
|---|---:|---|
| Critical | 0 | None |
| Major | 0 | None |
| Minor | 0 | None |

No defect has been found in the collector, storage layer, or security controls during live validation so far. The items below are **unfinished Gate 2 evidence**, not product defects.

## Not Yet Verified (blocks Gate 2, not a code defect)

| ID | Item | Why it's open |
|---|---|---|
| NOTVERIFIED-001 | 24-hour continuous read-only run | Longest observed run so far is ~21 minutes across two manual sessions |
| NOTVERIFIED-002 | Controlled restart with no message loss | Only "no duplicate" was provable (channel was quiet during the restart gap); no message arrived during the gap to test loss against |
| NOTVERIFIED-003 | Live edited-message version retention | No message in the observed window was edited by the channel owner; only unit-level fixture coverage exists |
| NOTVERIFIED-004 | Full 500-row manual reconciliation vs `@followgerry` | Only 20 of 500 rows were spot-checked so far |
| NOTVERIFIED-005 | Combined unit+integration test coverage ≥ 85% | Only the non-integration subset (50 tests, 73.91% coverage) was re-run in this pass |

**Disposition:** On 2026-09-06 the user explicitly chose to proceed to Phase 3 with NOTVERIFIED-001 through 005 still open, accepting them as known risk rather than waiting for completion. See [gate-2-checklist.md](gate-2-checklist.md) User Risk Acceptance Record. These items remain open and should be tracked to closure alongside Phase 3 work, since Phase 3 will consume this collector's output.

## Non-blocking Notices

| ID | Notice | Gate Impact | Follow-up Owner |
|---|---|---|---|
| NOTICE-001 | `docker compose run` failed early when Docker Desktop was not running (`npipe:////./pipe/dockerDesktopLinuxEngine` connect error). Resolved by starting Docker Desktop before retrying. | None — operator error, not a code defect | User, before each session |
| NOTICE-002 | First `collect` attempt failed with `password authentication failed for user "postgres"` because the `postgres_data` volume had already been initialized with a different password on an earlier run; PostgreSQL only applies `POSTGRES_PASSWORD` on first initialization of an empty data directory. Resolved by re-entering the original password (volume was not reset). | None once the correct password is used consistently | User |
| NOTICE-003 | A manual Ctrl+C during collection produces `KeyboardInterrupt` → process exit 130, which `telegram-bootstrap.ps1` surfaces as a thrown "Telegram bootstrap failed" error. This is expected shutdown behavior for a manual interrupt, not a crash. | None for Gate 2 | None planned |
