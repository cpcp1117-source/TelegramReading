# Phase 2 Test Evidence

- **Fixed Point:** `f4ccff6a039f42df35fc81054c9208861b42ecd3`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Target Channel:** `@followgerry` / `channel_id = 2439599598`
- **Credentials:** no credential value appears below; PostgreSQL and Telegram credentials were entered only at the local terminal

## 1. Automated Unit Test Suite (local, non-integration)

| Command | Result |
|---|---|
| `uv run pytest -m "not integration"` | PASS; 50 passed, 14 deselected |
| `uv run python scripts/secret_scan.py --root .` | PASS; 73 files, 0 findings |

Coverage for this non-integration subset is 73.91%, below the 85% project threshold — this is expected because the excluded `integration` tests (which require a live PostgreSQL instance) cover most of `db.py`, `outbox.py`, and parts of `telegram_storage.py`. The integration + non-integration combined suite (the actual Gate coverage gate) has not been re-run as part of this Phase 2 live-validation pass and remains **PENDING** for the Gate 2 package.

## 2. Live Collector Validation (manual, real Telegram account and channel)

| Run | Started (UTC) | Stopped (UTC) | Duration | Outcome |
|---|---|---|---|---|
| Run 1 | 2026-09-06 08:14:31 | 2026-09-06 08:23:54 | ~9m 23s | Clean; user-initiated Ctrl+C (exit 130); no crash |
| Run 2 (post-restart) | 2026-09-06 08:44:05 | in progress at time of writing | — | Backfill re-scan only; 0 new rows, 0 duplicates |

Startup evidence (Run 1), matches [models.py](../../src/telegram_trader/models.py) checkpoint/version schema:

```json
{"timestamp":"2026-09-06T08:14:31.368378+00:00","level":"INFO","logger":"__main__","message":"telegram collector ready","context":{"channel_id":2439599598,"backfill_seen":500,"checkpoint":6655}}
```

Shutdown evidence (Run 1): `asyncio.CancelledError` → `KeyboardInterrupt` → process exit 130 (SIGINT). This is the expected shutdown path for a manual Ctrl+C, not an application defect; the PowerShell wrapper surfaces any non-zero exit as a thrown error by design.

Restart evidence (Run 2), demonstrating checkpoint/backfill-overlap based resume:

```json
{"timestamp":"2026-09-06T08:44:17.035603+00:00","level":"INFO","logger":"__main__","message":"telegram collector ready","context":{"channel_id":2439599598,"backfill_seen":95,"checkpoint":6655}}
```

## 3. Database State Verification (`docker exec db psql`)

| Query | Result |
|---|---|
| `SELECT * FROM telegram_collector_checkpoints;` | `channel_id=2439599598, last_message_id=6655` |
| `SELECT COUNT(*), COUNT(DISTINCT message_id), MIN(message_id), MAX(message_id) FROM telegram_message_versions;` | `total=500, distinct=500, min_id=6142, max_id=6655` |
| `SELECT event_kind, is_backfill, COUNT(*) ... GROUP BY 1,2;` | 500 rows, all `BACKFILL` |
| `SELECT content_type, COUNT(*) ... GROUP BY 1;` | caption=209, text=150, image=108, empty=33 |
| Duplicate check: `GROUP BY channel_id, message_id, edit_version HAVING COUNT(*) > 1` (run after Run 2 restart) | 0 rows — **no duplicates after restart** |
| Edit-version distribution: `SELECT edit_version, COUNT(*) GROUP BY 1;` | `edit_version=0` for all 500 rows — **no edited message observed yet** |

## 4. Manual Content Spot-Check (sampled 20 of 500 rows)

Sampled the 20 most recent messages (`message_id` 6635–6655) with `source_date` converted to `Asia/Taipei`. User confirmed content correctness against the Telegram app; the only initial discrepancy was a display-timezone question (`+00` vs `+08`), which was resolved as expected `timestamptz` (UTC-stored) behavior, not a data defect. Full 500-row export for exhaustive manual reconciliation has not yet been requested/performed.

## 5. Outstanding Evidence Gaps for Gate 2

| Gap | Status |
|---|---|
| 24-hour continuous read-only run | **PENDING** — longest observed continuous run so far is ~21 minutes |
| Controlled restart — no missed/duplicate messages | **PARTIAL** — 0 duplicates confirmed after one restart; no message loss check yet possible because no new messages arrived between the two runs |
| Edited message retains original + new version | **PENDING** — no live edit event has occurred yet in the observed window; only unit-level coverage exists (`test_content_fingerprint_changes_for_edit` in `test_telegram_storage.py`) |
| Full manual message-count/ID reconciliation vs `@followgerry` | **PARTIAL** — 20/500 spot-checked; full reconciliation pending |
| Combined integration + unit suite coverage ≥ 85% | **PENDING** — not re-run in this pass |

No result in this document should be read as closing Gate 2; see [gate-2-checklist.md](gate-2-checklist.md) for the consolidated PASS/PENDING status.
