# Gate 3 Checklist

- **Phase:** Phase 3 — Channel Registry / Normalization
- **Fixed Point:** `eea4f29`
- **Gate Verdict:** `READY` — all 4 original Major items closed or reclassified as of 2026-09-08 (see [known-issues.md](known-issues.md)); P3-MAJOR-004 reclassified as structurally deferred to Phase 5, per explicit user decision below
- **User Acceptance:** `ACCEPTED` (2026-09-08)
- **Phase 4 Authorization:** `GRANTED`

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
| Critical = 0, Major = 0 | **MET** | Critical 0, Major 0 — P3-MAJOR-004 reclassified as structurally deferred (see below), not counted as an open Phase 3 gap |
| User acceptance recorded | RECORDED | see User Acceptance Record below |

## Path to READY

Three of the four original Major items were closed by code (fixture gap, retention, live reload). The fourth (P3-MAJOR-004, AI/media authorization enforcement) could not be built in Phase 3 — there is no Phase 5 code to gate it against. Two honest options were presented to the user:

1. **Treat P3-MAJOR-004 as structurally deferred, not open** — reclassify it out of the Major count on the basis that "no code exists to enforce against" isn't a gap in Phase 3's own deliverable, and mark Gate 3 `READY`.
2. **Leave it counted as open and risk-accept**, same pattern as [Phase 2](../phase-2/gate-2-checklist.md) — keep the honest `Major = 1` on the record, converting it into a Phase 5 Definition-of-Done item.

The user chose option 1.

## User Acceptance Record

- **Date:** 2026-09-08
- **Decision:** Option 1 — mark Gate 3 `READY`, treat P3-MAJOR-004 as structurally deferred rather than an open Phase 3 gap.
- **User's own words:** "標記成ready然後等到phase5時再來驗證" (mark it ready, and verify it once Phase 5 exists).
- **Binding consequence:** P3-MAJOR-004 is not forgiven — it converts into a mandatory Phase 5 Definition-of-Done item. Phase 5's own gate must include an explicit check that `ai_authorization`/`media_authorization` are enforced by code before any AI-processing or media-storage path may run. See [known-issues.md](known-issues.md) Resolved section.
- **Phase 4 Authorization:** `GRANTED` as of this record, per [ADR-0001](../adr/0001-sequential-stage-gates.md). Phase 2's 5 inherited risk-accepted gaps (see [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md)) remain open and carried forward unchanged — this acceptance does not resolve them.
