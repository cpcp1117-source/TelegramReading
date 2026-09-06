# Phase 1 Test Evidence

- **Fixed Point:** `6cd85b85e12be5810e6db34b862e24ba2df106f2`
- **Primary Run:** [GitHub Actions 33769849673](https://github.com/cpcp1117-source/TelegramReading/actions/runs/33769849673)
- **Started / Finished (UTC):** 2026-09-03 14:57:10 / 14:58:23
- **Credentials:** no external credentials; ephemeral DB credential included URL delimiters and was masked as `***`

## 1. Quality Job

| Command | Result |
|---|---|
| `uv sync --dev --locked --no-editable` | PASS; lockfile respected |
| `uv run ruff check .` | PASS; all checks passed |
| `uv run ruff format --check .` | PASS; 50 files formatted |
| `uv run mypy` | PASS; 22 source files, 0 issues |
| `uv run python scripts/secret_scan.py --root .` | PASS; 61 files, 0 findings |
| `uv run pip-audit . --strict --progress-spinner=off` | PASS; no known vulnerabilities |
| `uv run alembic upgrade head` | PASS; `0001` and `0002` applied |
| `uv run pytest --cov --cov-report=term-missing` | PASS; 37 tests; coverage 95.37% |
| `uv run alembic downgrade base` | PASS; `0002` and `0001` reverted |
| `uv run alembic upgrade head` | PASS; clean re-upgrade |

Coverage summary: 421 statements, 11 missed, 76 branches, 12 partial; displayed rounded coverage 95%, exact coverage 95.37%; required threshold 85%.

## 2. Compose Job

| Verification | Result | Key Evidence |
|---|---|---|
| Compose model | PASS | `docker compose config --quiet` |
| Blank credential | PASS | empty `POSTGRES_PASSWORD` rejected before startup |
| Special-character credential | PASS | generated value included `@:/#%?[]!+`; App and PostgreSQL authenticated |
| Clean build/start | PASS | PostgreSQL and App reached healthy state |
| Image scan | PASS | Trivy report: runtime image has 0 HIGH / CRITICAL findings |
| Image identity | PASS | Image ID recorded; unchanged after App restart |
| HTTP readiness | PASS | `{"status":"ready","database":"available"}` |
| No-egress operation | PASS | App edge network disconnected; only internal network remained; health passed |
| Duplicate replay | PASS | second replay `duplicate=true`; audit/outbox/receipt/checkpoint each remained 1 |
| Container restart | PASS | after restart and next event: audit/outbox/receipt/checkpoint each became 2 |
| Migration rollback/upgrade | PASS | both revisions downgraded then reapplied |
| Complete test suite | PASS | 37 passed; exact coverage 95.37% |
| Cleanup | PASS | containers, networks and volume removed |

## 3. Named Critical Scenarios

| Scenario | Test / Step | Result |
|---|---|---|
| Crash before commit | `test_crash_before_commit_rolls_back_audit_outbox_receipt_and_checkpoint` | PASS; all four records remain zero |
| Crash after commit | `test_crash_after_commit_replay_is_a_no_op` | PASS; replay creates no duplicate |
| Duplicate consumer delivery | `test_outbox_delivery_is_idempotent_per_consumer` | PASS |
| Checkpoint recovery | unit/integration test plus Compose container restart | PASS |
| Database constraints | `test_database_constraint_rejects_negative_checkpoint` | PASS |
| Append-only enforcement | `test_audit_event_cannot_be_updated_or_deleted` | PASS for Audit and Outbox update/delete |
| Sequence gap | `test_sequence_gap_fails_closed_without_advancing_checkpoint` | PASS |
| Original/edit/reply/duplicate fixture | `test_repository_fixture_covers_edit_reply_and_duplicate` | PASS |
| Sensitive log fields | `test_json_formatter_redacts_nested_sensitive_values` | PASS |
| Future capability config | offline config validation and dependency inventory | PASS; no provider SDK/client present |
| Password URL delimiters | structured SQLAlchemy URL plus clean Compose run | PASS; delimiters preserved as password data |
| Blank password | Compose fail-closed step and config unit test | PASS |

## 4. Local Windows Evidence

Local Windows validation used Python 3.11.15 and Docker Desktop 4.89.0:

```text
ruff: all checks passed
format: 43 files already formatted
mypy: 22 source files, no issues
secret scan: 62 files, 0 findings
pytest -m "not integration": 26 passed, 11 deselected
pip-audit project runtime dependencies: no known vulnerabilities
docker compose config --quiet: PASS
isolated PostgreSQL with password URL delimiters: 37 passed, coverage 95.37%
Alembic base -> head -> base -> head passed with a special-character credential
local App/DB status: healthy; HTTP readiness: ready / available
```

The isolated special-password Compose project used its own database volume and was removed after testing; the user's existing healthy Phase 1 volume was not changed.

## 5. Failed Runs and Remediation Record

| Run | Failure | Classification | Resolution |
|---|---|---|---|
| 33717402550 | Host readiness could not reach App on internal-only network | Resolved Major | Added separate App edge network; DB stayed internal-only |
| 33720752275 | Docker test target lacked pytest | Resolved Major | Added explicit test extra |
| 33722019840 | Nonexistent Trivy action tag | Resolved CI defect | Pinned immutable `0.35.0` release |
| 33722121456 | Trivy found fixable OS/Python HIGH vulnerabilities | Resolved Major | Updated base image, locked dependencies |
| 33722301084 | Base image still contained vulnerable build tooling | Resolved Major | Removed `setuptools` and `wheel` from runtime |
| 33722431754 | Cleanup invoked venv Python without pip | Resolved CI/build defect | Invoked system pip explicitly |
| 33722547591 | Redundant dependency audit attempted after no-egress isolation | Resolved CI defect | Kept strict dependency audit in connected quality job; kept Trivy on runtime image |
| 33722734397 | Full pre-report run | PASS | Superseded only by added version-reporting evidence |
| 33722971950 | Initial fixed-point run | PASS | Superseded by credential edge-case remediation |
| Local acceptance test | URL-delimiter password was parsed as connection URL structure | Resolved Major | Replaced raw URL interpolation with component config and `URL.create` |
| Local isolated rerun | First Docker run reused a stale test image | Resolved test-harness issue | Explicitly rebuilt test target before rerun; 37 tests passed |
| 33769849673 | Final remediation fixed-point run | PASS | Current Gate evidence source |

All resolved Major findings were followed by full affected-suite reruns. No failed result is presented as acceptance evidence.
