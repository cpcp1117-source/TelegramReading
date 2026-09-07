# Phase 3 Test Evidence

- **Fixed Point:** `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Credentials:** no credential value appears below; PostgreSQL and Telegram credentials were entered only at the local terminal / local `.env`

## 1. Static Checks

| Command | Result |
|---|---|
| `uv run ruff check .` | PASS; all checks passed |
| `uv run ruff format --check .` | PASS |
| `uv run mypy` | PASS; 31 source files, 0 issues |
| `uv run python scripts/secret_scan.py --root .` | PASS; 85 files, 0 findings |

## 2. Unit Suite (non-integration)

| Command | Result |
|---|---|
| `uv run pytest -m "not integration"` | PASS; 79 passed, 22 deselected |

Coverage includes: `evaluate_raw_collection` over every `(automation_authorization × gate_decision)` combination; `filter_authorized_targets` keeps only authorized targets and logs one warning per exclusion; multi-target entity resolution (username and numeric `PeerChannel`); forum-topic id extraction from `reply_to` including the `reply_to_top_id`-absent edge case; per-target backfill scoping via `reply_to`.

## 3. Integration Suite (disposable Postgres, `-m integration`)

| Command | Result |
|---|---|
| `uv run pytest -m integration` (against a throwaway container, correct `APP_DATABASE_*`/`TEST_DATABASE_URL`) | PASS; 24 passed, 79 deselected |

New coverage this phase: `channel_policies` round-trip including JSONB fields (`allowed_symbols`, `policy_detail`); `IntegrityError` for each new `CHECK` constraint (invalid `channel_type`, invalid authorization value, invalid `gate_decision`); a regression test that both real channels' currently-declared values evaluate `allowed=True` via `evaluate_raw_collection`; `resolve_effective_targets` both keeping only authorized targets and raising `ChannelPolicyError` when none remain; per-topic checkpoint isolation on two topics of the same channel; processor rejection of an unlisted `(channel_id, topic_id)`.

## 4. Migration Verification (disposable Postgres, not the user's real database)

| Step | Result |
|---|---|
| `alembic upgrade head` from empty (`0001`→`0005`) | PASS |
| `alembic downgrade -1` (`0005`→`0004`) | PASS |
| `alembic upgrade head` (`0004`→`0005`) | PASS |
| `alembic downgrade base` (full revert to empty) | PASS |
| `alembic upgrade head` (full replay `0001`→`0005`) | PASS |
| Seed row verification after upgrade | PASS — both `channel_policies` rows present with the exact values declared in the corresponding onboarding markdown at authoring time |

## 5. Live Production Verification

Applied to the real database (`telegram-trader-phase1-db-1`) via `.\scripts\telegram-bootstrap.ps1 collect` (which now force-rebuilds the collector image first — see [known-issues.md](../phase-2/known-issues.md) NOTICE for the stale-image incident this fixed):

| Check | Result |
|---|---|
| `alembic_version` after run | `0005_channel_policies` |
| `channel_policies` row count / content | 2 rows, both matching migration seed exactly |
| `@followgerry` collection continued | 507 rows, checkpoint `6662`, unaffected by the policy gate (as expected — `GRANTED`/`MONITOR_ONLY`) |
| bonnie-blockchain topic collection continued | 500 rows, checkpoint `99139`, unaffected by the policy gate |
| Duplicate check post-policy-gate | 0 duplicate `(channel_id, message_id, edit_version)` rows |
| Process shutdown | user-initiated Ctrl+C, expected `KeyboardInterrupt` → exit 130, not a crash |

No policy-exclusion warning was expected or observed in the database state (both channels remained fully collected), consistent with both being seeded as `GRANTED`/`MONITOR_ONLY`.

## 6. Known Gaps Not Covered By This Evidence

- No **live** test of the exclusion or fail-closed path (i.e., actually setting a channel's `gate_decision` to `PAUSED`/`REJECTED` or revoking `automation_authorization` against a real running collector to observe the skip-with-warning, or all-excluded-hard-failure, behavior in production logs). Both paths are covered by integration tests (`test_resolve_effective_targets_returns_only_authorized`, `test_resolve_effective_targets_fails_closed_when_none_authorized`) against a disposable database, but not live-demonstrated against the real collector process.
- Phase 2's 5 inherited gaps (24h soak, restart no-loss, live edit-version retention, full reconciliation, combined coverage) remain unaddressed — see [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md).
