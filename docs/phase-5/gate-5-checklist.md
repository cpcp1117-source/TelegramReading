# Gate 5 Checklist

- **Phase:** Phase 5 — AI Analysis / Public Market Data
- **Fixed Point:** `fcb6fd7`
- **Proposed Gate Verdict:** `READY` (risk-accepted) — pending user review below
- **User Acceptance:** *pending*
- **Phase 6 Authorization:** *pending*

| Gate Condition | Result | Evidence |
|---|---|---|
| Dynamic-scope symbol resolution against real, versioned `exchangeInfo` | PASS | real snapshot (571 symbols); 55/172 real `@followgerry` signals reach `VALIDATED` for the first time |
| Stale/missing snapshot fails closed, never guessed | PASS | `test_run_normalization_stale_snapshot_still_pending_market_data` |
| `market_snapshot` persisted, versioned, append-only | PASS | migration `0011`, trigger + test |
| ANALYSIS path outputs schema-valid Thesis with evidence/conditions/invalidation | PASS (code/test only) | `test_thesis_extraction.py`; **never run against real data** — see below |
| P3-MAJOR-004 DoD: `ai_authorization`/`media_authorization` enforced by real code | PASS (code/test only) | `test_ai_authorization.py`; **never live-exercised** — same caveat |
| Strategy Contract / Market Confirmation versioned, outputs WAIT/CONFIRMED/INSUFFICIENT_DATA/INVALIDATED | **NOT IMPLEMENTED** | deliberately paused, see below |
| Candidate Trade → Trade Intent only via allowlisted approval with expiry | **NOT IMPLEMENTED** | blocked on the above, same pause |
| No execution capability exists yet | PASS | unchanged from Phase 4 |
| Control Bot SL/TP editing (additional scope) works correctly | PASS | live-verified by the user, including a real bug found and fixed |
| Control Bot doesn't flood stale notifications after a backlog reprocessing | PASS | live-verified: zero notifications for 55 backlog signals |
| `collector`/`control-bot` resume after a host reboot | PASS | live-verified against a real machine reboot |
| Critical = 0, Major = 0 (new this phase) | PASS | 0 new Critical/Major findings this phase |
| Phase 2's five inherited NOTVERIFIED items | ALL CLOSED | two by real verification, three by user risk-acceptance |
| User acceptance recorded | *pending* | see below |

## Open Items Blocking a Clean READY

**FR-013/FR-014 not implemented (Strategy Contract, Market Confirmation, Candidate Trade).** This is the bulk of what "Phase 5" originally meant in [system-spec.md](../phase-0/system-spec.md)'s capability matrix ("Authorized AI/public market data"). Thesis extraction (FR-012) is done, but nothing consumes a Thesis yet -- it is inert data, exactly as [phase-kickoff.md](phase-kickoff.md) said it would be at this slice's boundary. [system-spec.md](../phase-0/system-spec.md)'s own `TBD-004` explicitly names Gate 5 as when Strategy Contract rules should be resolved; they are not.

This differs from both Phase 3's P3-MAJOR-004 (structurally impossible at the time) and Phase 4's P4-MAJOR-001 (buildable but simply not yet built): this gap is a **deliberate, user-directed re-scoping** away from the AI/ANALYSIS path mid-phase, not an oversight or a missed requirement. The rationale, as captured in this phase's commit history: `@followgerry` (`EXECUTION_SIGNAL`) is already fully structured and needs no AI to reach a usable v1, while bonnie-blockchain (`ANALYSIS`, free text) genuinely does need the AI path -- so the AI path was paused, not cancelled, in favor of shipping the smaller, already-ready thing first.

**Thesis extraction and its `ai_authorization` enforcement have never been run against real data.** Both are code-complete and unit/integration tested, but `SELECT count(*) FROM thesis` on the real database returns `0`. This is an honest gap worth naming: everything else materially "complete" in this phase's other deliverables has real-data evidence behind it (the whole point of this phase's verification work); this one item does not, purely because it was paused before ever getting that chance.

## Path to READY

Three options, same framing this project has used for every prior phase's open item:

1. **Resume the AI/ANALYSIS path now**, building FR-013/FR-014 before closing this Gate, so Phase 5 fully matches its original kickoff scope.
2. **Risk-accept the pause** and mark Gate 5 `READY` with these items explicitly named as open, carried forward to be resumed whenever the user chooses to revisit the AI/ANALYSIS path (no forced deadline, unlike P4-MAJOR-001's "before Production Gate 8" -- there is no Production Gate dependency on the ANALYSIS path the way there is on the EXECUTION_SIGNAL path).
3. **Reclassify as out of scope entirely**, removing it from Phase 5's Definition of Done and re-scoping the phase label itself -- not recommended, since the user has not said the AI path is cancelled, only paused, and TBD-004 remains a real, tracked question in system-spec.md.

## User Acceptance Record

*Awaiting the user's review of this checklist. Fill in below once decided:*

- **Date:**
- **Decision:** (which option above, or another)
- **User's own words:**
- **Binding consequence:**
- **Phase 6 Authorization:**
