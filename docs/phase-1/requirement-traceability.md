# Phase 1 Requirement Traceability

- **Source:** accepted Phase 0 Test Strategy、Architecture、Logical Data Model and user Stage-Gate plan
- **Fixed Point:** `6cd85b85e12be5810e6db34b862e24ba2df106f2`

| ID | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| P1-REQ-001 | Python project structure and locked dependencies | `pyproject.toml`、`uv.lock`、`src/telegram_trader/` | quality job locked sync, Ruff, mypy | PASS |
| P1-REQ-002 | PostgreSQL schema and reversible migrations | Alembic `0001`、`0002` | upgrade/rollback/upgrade in quality and Compose | PASS |
| P1-REQ-003 | Config must be offline-only | `config.py` | `test_config.py`; external/non-PostgreSQL hosts rejected | PASS |
| P1-REQ-004 | Structured logs redact sensitive values | `logging_config.py` | `test_logging_config.py` | PASS |
| P1-REQ-005 | Liveness/readiness report DB state | `app.py`、`healthcheck.py` | health unit tests and live Compose endpoint | PASS |
| P1-REQ-006 | Audit events are append-only | migration trigger、`audit.py` | update/delete DB integration test | PASS |
| P1-REQ-007 | Transactional outbox shares business transaction | `outbox.py`、`mock_telegram.py`、`0002` | crash-before/after tests and record counts | PASS |
| P1-REQ-008 | Consumer delivery is idempotent | `OutboxDeliveryReceipt` unique identity | duplicate consumer integration test | PASS |
| P1-REQ-009 | Mock replay is deterministic | stable source/audit/outbox IDs | identity unit test; Compose replay | PASS |
| P1-REQ-010 | Edit/reply/duplicate fixtures exist | `fixtures/mock_messages.json` | fixture coverage unit test | PASS |
| P1-REQ-011 | Checkpoint survives restart | persisted `ConsumerCheckpoint` | new-processor test and container restart | PASS |
| P1-REQ-012 | Gap fails closed | sequential lock/check | gap integration test | PASS |
| P1-REQ-013 | Clean Compose start and health | `Dockerfile`、`compose.yaml` | clean runner build/wait/readiness | PASS |
| P1-REQ-014 | Same runtime image across restart | image ID capture/check | Compose restart step | PASS |
| P1-REQ-015 | No external egress required | App operates on internal network after edge disconnect | no-egress Compose step | PASS |
| P1-REQ-016 | Secrets absent and credentials protected | `.gitignore`、scanner、masked ephemeral DB password | repo scan 0; logs show `***` | PASS |
| P1-REQ-017 | Dependency and image vulnerabilities checked | pip-audit、Trivy | no known dependencies; 0 HIGH/CRITICAL image findings | PASS |
| P1-REQ-018 | Overall coverage >=85% | pytest-cov config | 95.37% | PASS |
| P1-REQ-019 | Phase 2+ capabilities prohibited | no provider SDK/client; offline config | dependency/source inventory and config tests | PASS |
| P1-REQ-020 | Database credentials support URL special characters and reject blank values | component fields, `SecretStr`, SQLAlchemy `URL.create` | unit test, local isolated PostgreSQL, Compose CI | PASS |

## Acceptance Criteria

| AC | Given / When / Then | Evidence | Status |
|---|---|---|---|
| P1-AC-001 | Given a clean runner, when Compose builds and starts, then DB and App become healthy | Compose steps 4–8 | PASS |
| P1-AC-002 | Given the same mock event, when replayed, then only one receipt/audit/outbox exists | Compose replay and integration test | PASS |
| P1-AC-003 | Given a committed checkpoint, when App container restarts, then processing resumes at the next sequence | Compose restart; checkpoint becomes 2 | PASS |
| P1-AC-004 | Given staged audit/outbox/checkpoint writes, when commit crashes, then none persist | crash-before-commit integration test | PASS |
| P1-AC-005 | Given a committed event, when delivery/replay repeats, then duplicate effects are no-op | crash-after and delivery tests | PASS |
| P1-AC-006 | Given audit/outbox rows, when update/delete is attempted, then PostgreSQL rejects mutation | append-only integration test | PASS |
| P1-AC-007 | Given both migrations, when downgraded to base and upgraded, then all operations succeed | two CI jobs | PASS |
| P1-AC-008 | Given repository/runtime artifacts, when security scans run, then secret findings and HIGH/CRITICAL runtime findings are zero | scanner、pip-audit、Trivy | PASS |
| P1-AC-009 | Given App edge is disconnected, when internal health and data flow run, then no external egress is required | no-egress and subsequent Compose steps | PASS |
| P1-AC-010 | Given Phase 1 startup/config, when an external/future capability is configured, then it is absent or rejected | config/dependency tests | PASS |
| P1-AC-011 | Given a password containing URL delimiters, when App and migrations connect, then the raw password is preserved without changing URL structure | special-character CI Compose credential and 37-test database suite | PASS |
| P1-AC-012 | Given an empty database password, when Compose is rendered, then startup fails closed | blank credential CI step | PASS |
