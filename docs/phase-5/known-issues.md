# Phase 5 Known Issues

Phase 5 Gate has not been evaluated yet (Slice 2a/AI-ANALYSIS path is paused;
see [phase-kickoff.md](phase-kickoff.md)). This file tracks real findings
from verifying Slice 1 (Binance market data) against the real backlog, and
from the Control Bot's SL/TP-edit feature added pragmatically on top of
Phase 4 scope.

## Resolved

| ID | Item | Resolution |
|---|---|---|
| P5-MAJOR-001 | Slice 1's own Definition of Done (re-run the real `@followgerry` backlog against a populated Binance snapshot) was never actually confirmed to have succeeded | Found 2026-09-13 while planning the next step: commit `3ea3dac` bumped `normalizer`/`parser` versions to force a reprocessing pass, and `31fbaa4` fixed a run-order mistake (snapshot refresh had run after `normalize_content.py`, leaving the whole real backlog stuck at `PENDING_MARKET_DATA` under that version), but no document ever recorded whether the corrected re-run actually happened. Investigation found it silently had (real data already showed `v4`/`v3` fully processed), but this was undocumented until now. Re-verified end-to-end: fetched a fresh real Binance snapshot (571 active USDⓈ-M perpetuals), confirmed `@followgerry`'s real 172 signals under the current `parser_version=v3`/`normalizer_version=v4`: **55 `VALIDATED`, 117 `INCOMPLETE`, 0 `NEW`/`PENDING_MARKET_DATA`** -- the first real evidence that dynamic-scope resolution against real market data actually works end-to-end. |
| P5-NOTICE-001 | The long-running `app` service's Docker image was stale (built before a `config.py` refactor that added `database_host`/etc. fields), causing every `app`-image container (including one-off `docker compose run --rm app ...` script invocations) to resolve the database host to the pydantic-settings default `localhost` instead of the compose-provided `db`, and fail to connect | Same class of bug as the collector-image staleness fixed in commit `7e572cc`, just never hit for the `app` image before since nothing had used it for one-off scripts until now. Fixed by `docker compose build app` before running any script against it. Not committed as a code change -- purely a local rebuild step; worth remembering before any future one-off script run via the `app` image. |
| P5-MAJOR-002 | `load_pending_signals` (feeding the Control Bot's notification poll) had no staleness check, so a `parser_version` bump that reprocesses the entire real backlog (as P5-MAJOR-001 above does) would flood the user with a notification for every newly-created row that has never been requested before -- including the 55 real `VALIDATED` signals above, 35 of which are over 30 days old (oldest: 2026-06-18) and only 4 within the last 7 days | Found 2026-09-13 before ever starting the Control Bot against this reprocessed data -- would have surfaced up to 55 stale trade ideas as if they were fresh. `normalized_signals.expires_at` already existed as exactly the mechanism needed (`_compute_expires_at` in `parse_signals.py`, `source_date + 24h`, previously "a deterministic placeholder, not enforced by anything"): `load_pending_signals` now takes an explicit `now` and additionally filters `expires_at > now`, and `ControlBot.notify_pending_signals` passes its own injectable `self._clock()`. All 55 backlog signals are correctly excluded (each already past its 24h window relative to its real message date) without any special-casing -- the existing field just needed a reader. New integration test `test_load_pending_signals_excludes_already_expired_signal`; existing `load_pending_signals` call sites in `test_postgres_integration.py` updated to pass an explicit `now` consistent with their fixture dates. Verified: ruff, mypy, full suite (303 passed, unit + integration) against a disposable Postgres, 88.05% coverage; migration round-trip clean. **Not yet committed or live-verified against a running Control Bot** -- see Open below. |

## Open

| ID | Item | Note |
|---|---|---|
| P5-OPEN-001 | The Control Bot has not yet been started against the reprocessed real backlog with the `expires_at` fix in place | Should be live-verified once running: confirm it does *not* notify on any of the 55 backlog `VALIDATED` signals, and does still notify correctly on a genuinely fresh signal. |
| P5-OPEN-002 | Normalization/parsing was re-run directly against the real database via `docker compose run --rm app ...` (not through `telegram-bootstrap.ps1`, which has no subcommand for this) | Matches the same commands used in Phase 4 for the same purpose; no new script gap, just noting the exact invocation path for future reference. |

## Inherited From Phase 4 (still open)

Per [../phase-4/known-issues.md](../phase-4/known-issues.md): P4-MAJOR-001 (edited-message/supersede linking), and Phase 2's five risk-accepted NOTVERIFIED items, both carried forward unchanged.
