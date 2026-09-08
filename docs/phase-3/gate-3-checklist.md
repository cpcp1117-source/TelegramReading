# Gate 3 Checklist

- **Phase:** Phase 3 — Channel Registry / Normalization
- **Fixed Point:** `eea4f29`
- **Gate Verdict:** `NOT READY` — 1 Major item open (see [known-issues.md](known-issues.md)); the other 3 (fixture gap, retention, live reload) were closed on 2026-09-08
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
| Retention configured and enforced | PASS | migration 0006 + `scripts/retention_cleanup.py`; age-gated trigger bypass verified in a dedicated regression test |
| Live policy reload | PASS (shrink-only) | collector polls `channel_policies` every 5 min and excludes a paused/revoked target without restart; residual gap (API/media traffic not stopped) tracked as P3-MINOR-003 |
| Critical = 0, Major = 0 | **NOT MET** | Critical 0, 1 Major remains (P3-MAJOR-004 — see [known-issues.md](known-issues.md)), but it has nothing to enforce against until Phase 5 exists |
| User acceptance recorded | PENDING | not yet recorded |

## Path to READY

Three of the four original Major items are closed. The one remaining item (P3-MAJOR-004, AI/media authorization enforcement) cannot actually be built yet — there is no Phase 5 code to gate. Two honest options:

1. **Treat P3-MAJOR-004 as structurally deferred, not open** — reclassify it out of the Major count on the basis that "no code exists to enforce against" isn't a gap in Phase 3's own deliverable, and mark Gate 3 `READY` once the user reviews everything above.
2. **Leave it counted as open and risk-accept**, same pattern as [Phase 2](../phase-2/gate-2-checklist.md) — keep the honest `Major = 1` on the record, with the explicit understanding that it converts into a Phase 5 Definition-of-Done item rather than something Phase 3 itself can close.

Either way, the user's explicit acceptance is still required before Phase 4 begins.

## User Acceptance Record

Not yet recorded. Per [ADR-0001](../adr/0001-sequential-stage-gates.md), Phase 4 code/credential integration should not begin until the user reviews the items above and records an explicit decision.
