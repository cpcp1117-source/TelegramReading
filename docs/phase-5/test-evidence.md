# Phase 5 Test Evidence

- **Fixed Point:** `fcb6fd7`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Credentials:** no credential value appears below; PostgreSQL, Telegram, Control Bot, and OpenAI credentials were entered only at the local terminal / local `.env`

## 1. Static Checks

| Command | Result |
|---|---|
| `uv run ruff check .` | PASS; all checks passed |
| `uv run ruff format --check .` | PASS; 116 files already formatted |
| `uv run mypy` | PASS; 53 source files, 0 issues |
| `uv run python scripts/secret_scan.py --root .` | PASS; 130 files, 0 findings |
| `uv run pip-audit . --strict --progress-spinner=off` | PASS; no known vulnerabilities |

## 2. Unit Suite (non-integration)

| Command | Result |
|---|---|
| `uv run pytest -m "not integration"` | PASS; 217 passed, 86 deselected |

New coverage this phase: Binance `exchangeInfo` client parsing and active-perpetual filtering, snapshot staleness (`test_binance_market_data.py`); dynamic-scope resolution against a real-shaped snapshot including the ambiguous-match and stale-snapshot cases (`test_normalization.py`); `ai_authorization`'s channel- and message-level fail-closed gates, including the media-attached/`media_authorization` conservative reading (`test_ai_authorization.py`, 20 tests); Thesis extraction's schema validation rejecting unknown fields, invented symbols, and non-verbatim evidence quotes (`test_thesis_extraction.py`, 15 tests); Control Bot's editable-draft flow (field-select callback, 120s reply window, expiry, and `_format_decimal`'s fixed-point rendering with no scientific notation) (`test_control_bot.py`).

## 3. Integration Suite (disposable Postgres, `-m integration`)

| Command | Result |
|---|---|
| `uv run pytest -m integration` (against a genuinely separate, throwaway Postgres container -- not the project's shared `docker compose --profile test` service, which points at the same real database as `db`; see §6 Bug 1) | PASS; 86 passed |
| Combined (`uv run pytest --cov --cov-report=term-missing`, unit + integration together) | PASS; 303 passed, 88.05% coverage (threshold 85%) |

New coverage this phase: `run_normalization` resolving `BINANCE_USDM_ACTIVE_PERPETUAL` against a real-shaped persisted snapshot, staying `PENDING_MARKET_DATA` for a stale one; Thesis extraction's full pipeline against disposable Postgres (authorized/unauthorized channel, media-gated, provider-failure, and idempotency-under-version-bump cases); `signal_decision_edits`' append-only trigger and cascade delete; `load_pending_signals`'s new `expires_at` filter (`test_load_pending_signals_excludes_already_expired_signal`) plus the existing call sites updated to pass an explicit `now` consistent with their fixture dates.

## 4. Migration Verification (disposable Postgres, not the user's real database)

| Step | Result |
|---|---|
| `alembic upgrade head` from empty (`0001`→`0013`) | PASS |
| `alembic downgrade base` (full revert to empty) | PASS |
| `alembic upgrade head` (full replay `0001`→`0013`) | PASS |

Re-run on a genuinely separate throwaway Postgres container after the `expires_at` fix, and again after confirming the real database's own migration head via `docker compose run --rm app alembic current`.

## 5. Live Production Verification

Applied to the real database (`telegram-trader-phase1-db-1`) and the real Telegram channels/bot, across several sessions as this phase's work landed:

| Check | Result |
|---|---|
| Real Binance snapshot fetch | `GET /fapi/v1/exchangeInfo` (public, no key) -- 571 active USDⓈ-M perpetuals persisted |
| Real backlog reprocessing (`normalize_content.py`/`parse_signals.py` under `v4`/`v3`) | `@followgerry`: 55/172 real signals reach `VALIDATED` for the first time, 117 `INCOMPLETE`, 0 stuck at `PENDING_MARKET_DATA` |
| Control Bot against the reprocessed backlog | Zero notifications sent for the 55 backlog signals (all correctly past their 24h `expires_at`); zero errors over multiple poll cycles |
| Control Bot SL/TP edit flow | User live-tested editing stop-loss then take-profit; found and confirmed the fix for a real display-inconsistency bug (`10` vs `10.00000000`) |
| Live edited-message version retention (Phase 2 NOTVERIFIED-003) | `@followgerry` message_id `6682`, edited twice by its author 2026-09-11; all three versions correctly retained with accurate `edit_date` timestamps |
| Reboot recovery (`restart: unless-stopped`) | User rebooted the real machine; `db`/`collector`/`control-bot` all came back automatically ~3.5 minutes after boot, `RestartCount=0` |
| Thesis extraction | **Never run against real data** -- `ai_authorization`/`media_authorization` are `GRANTED` for bonnie-blockchain in the real `channel_policies` table, but `SELECT count(*) FROM thesis` returns `0`. Deliberately paused before ever being exercised live. |

## 6. Real Bugs / Process Findings This Phase

| # | Finding | Root Cause | Fix | Evidence It's Fixed |
|---|---|---|---|---|
| 1 | This project's `docker compose --profile test` service shares the **same** real, persistent `db` as `collector`/`control-bot` -- not a disposable container the way earlier phase reports' "disposable Postgres" language implied. Its integration-test fixture opens with `TRUNCATE ... CASCADE` across nearly every table. | The compose project defines one `db` service; `test`'s `depends_on: db` is that same service, not a separate ephemeral one. Earlier phases' CI only ever exercised this safely inside GitHub Actions' fresh, disposable runners. | Never ran `docker compose --profile test run --rm test pytest` locally against the real `db`. Instead started a genuinely separate, ad hoc Postgres container (`docker run ... -p 55432:5432 postgres:16.6-alpine`), pointed `.venv-ci` at it via `TEST_DATABASE_URL`/`APP_DATABASE_*`, ran the full suite there, then tore it down. | Real `db`'s row counts (172 signals, hundreds of real messages) were confirmed unchanged before and after every local test run this phase. |
| 2 | The long-running `app` service's Docker image was stale (predated a `config.py` refactor), so `docker compose run --rm app ...` resolved the database host to `localhost` instead of `db` and failed to connect -- same class of bug as the collector-image staleness previously fixed in commit `7e572cc` | Image not rebuilt after the source changed | `docker compose build app` before any one-off script run via that image | `hasattr(Settings(), 'database_host')` and a real connection both confirmed after rebuild |
| 3 | An orphaned Control Bot container from an earlier manual `telegram-bootstrap.ps1 control-bot` session (never Ctrl+C'd) was still running when a second instance was started for live verification -- two live instances briefly held the same Telegram bot token/session | `docker compose run` leaves a live container behind if not explicitly stopped; it does not appear under the named `docker compose ps` service view | Found via `docker compose ps -a`; stopped and removed | Confirmed only one `control-bot` container running afterward |
| 4 | `load_pending_signals` had no staleness check, so reprocessing the real backlog under a bumped `parser_version` would have flooded the user with a notification for every one of the 55 newly-`VALIDATED` signals -- 35 of them over 30 days old | `normalized_signals.expires_at` existed but was "a deterministic placeholder, not enforced by anything" | `load_pending_signals` now takes an explicit `now` and filters `expires_at > now`; `ControlBot` passes its own clock | `test_load_pending_signals_excludes_already_expired_signal`; live-verified zero notifications sent |
| 5 | Editing stop-loss then take-profit displayed the stop-loss inconsistently (`10` then `10.00000000`) | Postgres's `Numeric(20,8)` pads a value to its full declared scale on read-back; the in-memory value just typed hadn't gone through that round-trip yet | `_format_decimal`: fixed-point, trailing-zeros-stripped, explicitly avoiding `Decimal.normalize()`'s scientific notation | User's own live re-test confirmed both edits now display identically |

Findings 1-3 are process/tooling gaps found while doing this phase's verification work, not defects in the shipped product. Findings 4-5 are real product bugs, both found and fixed before ever reaching the live Control Bot in finding 4's case, and via the user's own live re-test in finding 5's case.

Full detail on all of the above, plus the Phase 2 NOTVERIFIED closures, is in [known-issues.md](known-issues.md).
