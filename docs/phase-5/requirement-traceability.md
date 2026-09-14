# Phase 5 Requirement Traceability

- **Source:** [system-spec.md](../phase-0/system-spec.md) FR-002, FR-012/FR-013/FR-014, BR-012, BR-013, [phase-kickoff.md](phase-kickoff.md) Slice 1/Slice 2a scope, plus additional scope confirmed with the user mid-phase (Control Bot SL/TP editing)
- **Fixed Point:** `fcb6fd7`

## Slice 1 — Binance Public Market Data (complete, live-verified)

| ID | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| P5-REQ-001 (FR-002) | Dynamic-scope (`BINANCE_USDM_ACTIVE_PERPETUAL`) symbol resolution must only resolve against fresh/versioned real `exchangeInfo` data, never a guess | `binance_market_data.py`'s `BinanceMarketDataClient`/`refresh_snapshot` (public `GET /fapi/v1/exchangeInfo`, no API key); `resolve_symbol` in `normalization.py` matches candidate aliases against `snapshot.active_symbols`, `VALID` only for exactly one match | `test_binance_market_data.py`, `test_normalization.py`; **live**: real snapshot fetched (571 active USDⓈ-M perpetuals), real `@followgerry` backlog reprocessed under `normalizer_version=v4`/`parser_version=v3` -- 55/172 real signals reach `VALIDATED` for the first time (was 0% under Phase 4's fail-closed `PENDING_MARKET_DATA`) | PASS |
| P5-REQ-002 (BR-012 fail-closed on stale dependency) | A stale/missing snapshot must not be treated as valid for resolution | `load_latest_snapshot(session, clock, max_age_seconds)` returns `None` for a snapshot older than the caller's freshness window; `run_normalization` logs a warning and falls back to `PENDING_MARKET_DATA` rather than resolving against stale data | `test_run_normalization_stale_snapshot_still_pending_market_data` | PASS |
| P5-REQ-003 | `market_snapshot` must be a persisted, versioned, append-only record | `binance_symbol_snapshots` table (migration `0011`), update-rejection trigger matching the project's append-only convention | `test_postgres_integration.py` UPDATE-rejection test | PASS |

## Slice 2a — Thesis Extraction (code-complete, paused before any real run)

| ID | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| P5-REQ-004 (FR-012) | ANALYSIS path must output schema-valid Thesis with evidence, conditions, invalidation, and confidence/missing-data status | `thesis_extraction.py::run_thesis_extraction`/`parse_llm_thesis_response`; `thesis` table (migration `0012`) with a structured `conditions[]` schema; OpenAI Structured Outputs (`response_format=json_schema`, strict mode) rejects unknown fields, invented symbols, and evidence quotes not verbatim in the source text | `test_thesis_extraction.py` (15 tests), integration tests against disposable Postgres | **PARTIAL** -- code-complete and tested, but **never run against the real backlog**: `SELECT count(*) FROM thesis` on the real database returns `0`. No real OpenAI call has ever been made by this project. Deliberately paused, not abandoned -- see Phase Report §3. |
| P5-REQ-005 (P3-MAJOR-004 DoD item, mandatory per [phase-kickoff.md](phase-kickoff.md)) | `ai_authorization`/`media_authorization` must be enforced by real code before any AI-processing path runs, closing Phase 3's structurally-deferred gap | `ai_authorization.py::evaluate_channel_ai_authorization`/`evaluate_message_ai_processing`: fail-closed on missing policy row, wrong `channel_type`, `ai_authorization != GRANTED`, or (for messages with attached media) `media_authorization != GRANTED` -- checked before every `thesis_extraction.py` call | `test_ai_authorization.py` (20 tests) | PASS on code/test grounds -- **but never live-exercised**, since Slice 2a itself was never run against real data (same caveat as P5-REQ-004). The gate is real and tested; it has simply never had a live opportunity to actually block or allow anything yet. |
| P5-REQ-006 (FR-013) | Strategy Contract / Market Confirmation must be versioned and output `WAIT`/`CONFIRMED`/`INSUFFICIENT_DATA`/`INVALIDATED` against fresh market data | Not built | N/A | **NOT IMPLEMENTED** -- deliberately out of scope; deprioritized when the user re-scoped toward `@followgerry` v1 (see Phase Report §1). [system-spec.md](../phase-0/system-spec.md)'s own `TBD-004` ("Strategy Contract rules per channel") explicitly names Gate 5 as when this should resolve -- it has not. |
| P5-REQ-007 (FR-014) | Candidate Trade may only become a Trade Intent via allowlisted-user approval with an expiry | Not built (no Candidate Trade concept exists yet) | N/A | **NOT IMPLEMENTED** -- blocked on P5-REQ-006, same deliberate pause |

## Additional Scope Confirmed With the User Mid-Phase (not in original Phase 5 kickoff)

| ID | Requirement | Implementation | Verification | Status |
|---|---|---|---|---|
| P5-REQ-008 | Control Bot must let the user edit a signal's stop-loss/take-profit before approving, for the `@followgerry` (`EXECUTION_SIGNAL`) v1 workflow | `signal_decision_edits` table (migration `0013`, append-only, one row per edit with a full snapshot); `signal_decisions.py::load_current_draft`/`create_edit`; `control_bot.py`'s edit callback family + in-memory pending-edit slot (120s reply window) | `test_control_bot.py`, `test_postgres_integration.py`; **live-verified** by the user, including a real display-formatting bug found and fixed (`10` vs `10.00000000`, `_format_decimal`) | PASS |
| P5-REQ-009 | The Control Bot's notification poll must not surface a signal past its own `expires_at`, even after a `parser_version` bump reprocesses the whole real backlog | `load_pending_signals(session, now=...)` filters `expires_at > now`; `ControlBot.notify_pending_signals` passes its own injectable clock | `test_load_pending_signals_excludes_already_expired_signal`; **live-verified**: Control Bot run against the real reprocessed backlog (55 `VALIDATED` signals, most weeks old) sent zero notifications, as expected | PASS |
| P5-REQ-010 | `collector`/`control-bot` should resume automatically after a host/Docker reboot | `compose.yaml` `restart: unless-stopped` (was `"no"`) | **Live-verified against a real machine reboot**: all three containers (`db`/`collector`/`control-bot`) came back automatically ~3.5 minutes after boot with `RestartCount=0` | PASS (Docker Desktop's own "start at login" remains a separate, user-managed setting) |

## Phase 2 Inherited Items — Follow-up This Phase

Per [known-issues.md](known-issues.md)'s "Phase 2 NOTVERIFIED Follow-up" section: all five of Phase 2's originally risk-accepted items are now closed -- NOTVERIFIED-003 (live edit retention) and NOTVERIFIED-005 (coverage ≥85%) by real verification; NOTVERIFIED-001 (24h soak), NOTVERIFIED-002 (restart no-loss), and NOTVERIFIED-004 (500-row reconciliation) by explicit user risk-acceptance, given how the system is actually used (not kept on 24/7; the workflow only ever acts on currently-available data).

## Still Open, Carried Forward Unchanged

Per [known-issues.md](known-issues.md): **P4-MAJOR-001** (edited-message/supersede lifecycle linking) remains open, mandatory to close before Production Gate 8. **TBD-004** (Strategy Contract rules per channel) remains unresolved, now explicitly tied to whenever the AI/ANALYSIS path is resumed.
