# Gate 3 Checklist

- **Phase:** Phase 3 — Channel Registry / Normalization
- **Fixed Point:** `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813`
- **Gate Verdict:** `NOT READY` — 1 channel-level requirement unmet (`@followgerry` fixtures), 4 Major items open (see [known-issues.md](known-issues.md))
- **User Acceptance:** `PENDING`
- **Phase 4 Authorization:** `NOT GRANTED`

| Gate Condition | Result | Evidence |
|---|---|---|
| Identity resolved by numeric channel ID for every configured channel | PASS | both `@followgerry` and bonnie-blockchain topic 21 confirmed via `dialogs`/`preview` |
| Required authorization scopes granted for every configured channel | PASS | all four scopes `GRANTED` for both channels — self-declared, not third-party/legally reviewed (same caveat as Phase 2) |
| Valid symbol scope configured for every configured channel | PASS | `BINANCE_USDM_ACTIVE_PERPETUAL` (followgerry) / `STATIC_ALLOWLIST` with BTCUSDT+ETHUSDT (bonnie-blockchain) — both now in `channel_policies`, not just markdown |
| 20 fixtures reviewed per channel | **FAIL for `@followgerry` (1/20)**, PASS for bonnie-blockchain (20/20, with the diversity caveat noted in its onboarding doc) | [monster-currency-universe.md](../phase-0/channels/monster-currency-universe.md) §6 |
| Channel Policy allowlist enforced by code, not just documented | PASS | `channel_policy.py` + migration 0005, live-verified against the real database |
| Critical parser false positive = 0 | N/A | no parser exists yet (Phase 4 scope) |
| Retention configured | PARTIAL | declared (7 days both channels) but not technically enforced by any deletion job |
| Critical = 0, Major = 0 | **NOT MET** | Critical 0, but 4 Major items open (see [known-issues.md](known-issues.md)) |
| User acceptance recorded | PENDING | not yet recorded |

## Path to READY

Two ways to close this Gate, in order of rigor:

1. **Close `@followgerry`'s fixture gap** — gather/anonymize 19 more representative message samples for `@followgerry` (same process already used for bonnie-blockchain via the `preview` command), update [monster-currency-universe.md](../phase-0/channels/monster-currency-universe.md) §5, then re-evaluate this Gate. This is the only item blocking a clean PASS on the fixture requirement specifically.
2. **Risk-accept**, same pattern as [Phase 2](../phase-2/gate-2-checklist.md) — explicitly acknowledge the fixture gap and the 4 Major items as accepted risk and proceed to Phase 4 anyway. Given Phase 4 (signal parsing) will need `@followgerry`'s fixtures directly to build and test its parser, deferring this gap into Phase 4 risks discovering fixture-format surprises mid-parser-development rather than before — a genuine cost specific to this particular gap, unlike some of Phase 2's gaps which were more about time-based confidence (soak duration) than missing raw material.

## User Acceptance Record

Not yet recorded. Per [ADR-0001](../adr/0001-sequential-stage-gates.md), Phase 4 code/credential integration should not begin until the user reviews the items above and either directs fixture completion or explicitly risk-accepts, the same way they did for Phase 2.
