# Gate 3 Checklist

- **Phase:** Phase 3 — Channel Registry / Normalization
- **Fixed Point:** `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813`
- **Gate Verdict:** `NOT READY` — 3 Major items open (see [known-issues.md](known-issues.md)); the `@followgerry` fixture gap (previously the 4th) was closed on 2026-09-08
- **User Acceptance:** `PENDING`
- **Phase 4 Authorization:** `NOT GRANTED`

| Gate Condition | Result | Evidence |
|---|---|---|
| Identity resolved by numeric channel ID for every configured channel | PASS | both `@followgerry` and bonnie-blockchain topic 21 confirmed via `dialogs`/`preview` |
| Required authorization scopes granted for every configured channel | PASS | all four scopes `GRANTED` for both channels — self-declared, not third-party/legally reviewed (same caveat as Phase 2) |
| Valid symbol scope configured for every configured channel | PASS | `BINANCE_USDM_ACTIVE_PERPETUAL` (followgerry) / `STATIC_ALLOWLIST` with BTCUSDT+ETHUSDT (bonnie-blockchain) — both now in `channel_policies`, not just markdown |
| 20 fixtures reviewed per channel | PASS for both — `@followgerry` now has 21 (1 synthetic + 20 real, pulled 2026-09-08), bonnie-blockchain has 20 (both with diversity caveats noted in their own onboarding docs) | [monster-currency-universe.md](../phase-0/channels/monster-currency-universe.md) §5–6 |
| Channel Policy allowlist enforced by code, not just documented | PASS | `channel_policy.py` + migration 0005, live-verified against the real database |
| Critical parser false positive = 0 | N/A | no parser exists yet (Phase 4 scope) |
| Retention configured | PARTIAL | declared (7 days both channels) but not technically enforced by any deletion job |
| Critical = 0, Major = 0 | **NOT MET** | Critical 0, but 3 Major items remain open (P3-MAJOR-002/003/004 — see [known-issues.md](known-issues.md); P3-MAJOR-001 fixture gap is resolved) |
| User acceptance recorded | PENDING | not yet recorded |

## Path to READY

The fixture gap (this Gate's only channel-level requirement failure) is now closed. What remains is the same choice Phase 2 faced:

1. **Close the remaining 3 Major items first** — build retention enforcement, live/dynamic policy reload, and wire AI/media authorization checks into whatever Phase 5 code eventually needs them (the last one may be more natural to close *as part of* Phase 5, when there's finally code to gate).
2. **Risk-accept**, same pattern as [Phase 2](../phase-2/gate-2-checklist.md) — explicitly acknowledge the 3 Major items as accepted risk and proceed to Phase 4 anyway. Unlike the fixture gap (which would have directly blocked Phase 4 parser development), these three are more independent of Phase 4's actual work: retention and live-reload are operational hardening, and AI/media authorization enforcement has nothing to enforce against until Phase 5 exists.

## User Acceptance Record

Not yet recorded. Per [ADR-0001](../adr/0001-sequential-stage-gates.md), Phase 4 code/credential integration should not begin until the user reviews the items above and either directs further gap closure or explicitly risk-accepts, the same way they did for Phase 2.
