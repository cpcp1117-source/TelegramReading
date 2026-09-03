# Phase 1 Known Issues

## Open Product Findings

| Severity | Count | Items |
|---|---:|---|
| Critical | 0 | None |
| Major | 0 | None |
| Minor | 0 | None |

## Non-blocking Notices

| ID | Notice | Gate Impact | Follow-up Owner |
|---|---|---|---|
| NOTICE-001 | GitHub reports that transitive action dependencies still target deprecated Node.js 20 but are forced onto Node.js 24. The jobs pass and this code is controlled by third-party actions. | None for Gate 1 | Maintainer monitors action releases |

## Closed Findings

All Major findings from failed Phase 1 runs are closed and regression-tested; see [test-evidence.md](test-evidence.md#5-failed-runs-and-remediation-record).

- Windows Docker Desktop startup was repaired; local App/DB health passed.
- `P1-MAJOR-001` special-character database password parsing was fixed by structured URL construction and verified locally and in CI.
