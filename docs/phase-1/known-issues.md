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
| NOTICE-002 | This Windows workstation's Docker Desktop 4.71.0 cannot start its Inference manager because the stale `dockerInference` socket cannot be accessed. No factory reset or destructive deletion was performed. | Clean-runner Gate evidence is complete; local Docker convenience remains unavailable | Workstation owner / Docker Desktop support |

`NOTICE-002` is an external workstation condition, not a repository defect. Before Phase 2 relies on local containers, use a recoverable Docker Desktop repair/update path or continue on a clean Linux host. It does not authorize Phase 2 by itself.

## Closed Findings

All Major findings from failed Phase 1 runs are closed and regression-tested; see [test-evidence.md](test-evidence.md#5-failed-runs-and-remediation-record).
