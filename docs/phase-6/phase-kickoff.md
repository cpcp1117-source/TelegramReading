# Phase 6 Kickoff — Risk Engine / Binance Testnet

## Status

- Phase: `Phase 6 — Risk/Binance Testnet` (per [system-spec.md](../phase-0/system-spec.md) §14 Implementation Plan, row 6)
- Status: `KICKOFF`
- Gate 6: `NOT EVALUATED`
- Baseline: Phase 5, accepted `READY` (risk-accepted) on 2026-09-14 (see [../phase-5/gate-5-checklist.md](../phase-5/gate-5-checklist.md))
- Authorized by: User, 2026-09-14
- Active branch: not yet created

This is a materially higher-stakes phase than any before it: it is the first time this project touches real Binance API credentials, places real orders (even if only against Testnet, never real money), and runs deterministic risk math (position sizing, daily kill switch, leverage) whose bugs have direct financial-shaped consequences even in a sandbox. Nothing here should be rushed past the same rigor every prior phase got.

## Inherited Items From Prior Phases

- **P4-MAJOR-001** (edited-message/supersede lifecycle linking) remains open, still mandatory to close before Production Gate 8. Not blocking for Phase 6, but must not be forgotten — if either onboarded channel is ever observed to produce an edited signal message, this becomes immediately relevant regardless of what phase is active.
- **FR-013/FR-014** (Strategy Contract, Market Confirmation, Candidate Trade — the paused AI/ANALYSIS path) and **TBD-004** (Strategy Contract rules per channel) remain open with no forced deadline, per Gate 5's risk acceptance. Phase 6 does not need to touch these — the Risk Engine/Execution Gateway consume `TradeIntentRequested.v1`, which for now can only ever originate from the `EXECUTION_SIGNAL` path (`@followgerry`), since Candidate Trade (the ANALYSIS path's route to a Trade Intent) doesn't exist.
- **P5-OPEN-001** (Control Bot's "does it still notify on a genuinely fresh signal" verification) remains open, passive, waiting on a real new `@followgerry` signal.

## Why "Deferred to Phase 6" Was the Right Call, Not a Shortcut

Phase 4's own kickoff explicitly wrestled with this: could a `trade_intent` **record** exist earlier than Phase 6, even without a Risk Engine to consume it? It decided no — [architecture.md](../phase-0/architecture.md) names `risk-engine` as `TradeIntentRequested.v1`'s only consumer starting Phase 6, and Phase 3 had already drawn criticism (P3-MAJOR-004) for shipping columns nothing consumed yet. So both the record and its consumer are deliberately built together, now.

## Objective

Per [system-spec.md](../phase-0/system-spec.md) FR-016 (Risk Gate), FR-017 (Execution Gateway), FR-018–FR-023 (idempotency, protection orders, reconciliation, pause semantics, audit, environment isolation), and [architecture.md](../phase-0/architecture.md)'s `risk-engine`/`execution-gateway` module boundary:

1. **Risk Engine**: deterministic, fixed-rule evaluation of a `TradeIntentRequested.v1` event into a `RiskDecisionIssued.v1` (`APPROVED`/`REJECTED` with reason codes), enforcing BR-006 (freshness), BR-007 (one-way conflict), BR-008 (5x isolated leverage), BR-009 (3% single-trade risk / 10% single-trade initial margin / 30% total initial margin), BR-010 (max 3 concurrent positions, including pre-existing manual ones), BR-011 (Asia/Taipei daily kill switch at -6% equity), BR-012 (fail closed on any stale/unhealthy/unknown dependency), BR-013 (no LLM credential or Risk Rule write access — already true by construction, since nothing AI-touched reaches this module).
2. **Execution Gateway**: a Binance USDⓈ-M Futures Testnet adapter that takes an `APPROVED` Risk Decision and places a real (Testnet) entry order with a deterministic `clientOrderId`, establishes an exchange-side Protection Order (stop) within a 5-second deadline of any fill (FR-019/NFR-003) or Emergency Closes, and reconciles order/fill/position state against both the User Data Stream and REST on every restart (FR-020, NFR-005).
3. **System Pause semantics** (FR-021): the Control Bot's already-implemented `pause`/`resume`/`close`/`close_all` commands finally have a real Execution Gateway to act against, for the first time since Phase 4 built them as honest stubs.
4. **Hard Testnet/Production isolation** (FR-023, BR-014, [credential-handoff.md](../phase-0/credential-handoff.md) §5): a Testnet-only key created fresh for this phase, mounted only into the Execution Gateway, with base URL/credential/database-namespace separation enforced by code and tests, not just convention — TM-012's threat ("Testnet config points to Production") is a named, Gate-6-tagged risk in [threat-model.md](../phase-0/threat-model.md).

## Proposed Slice Order

Following this project's established pattern (Phase 5 split "no credential needed" work from "needs a new credential" work, and did the former first): [architecture.md](../phase-0/architecture.md) line 48 lists the Risk Engine's own credential requirement as `None` — only the Execution Gateway needs the Binance Testnet key. This suggests the same split applies cleanly here:

- **Slice 1 (proposed): Risk Engine only.** Build `trade_intent`/`risk_decision` against their already-fully-specified schemas ([logical-data-model.md](../phase-0/logical-data-model.md) §3.5), wire it to consume `VALIDATED` `@followgerry` signals (the only real `TradeIntentRequested.v1` source that exists), and verify all the BR-006/007/008/009/010/011/012 rules against golden/negative fixtures. No Binance credential touched at all — testable entirely against synthetic account/market snapshots, the same way Slice 1 of Phase 5 was pure logic before any real API call.
- **Slice 2+ (proposed, needs the Testnet credential): Execution Gateway.** Binance Testnet adapter, order placement, Protection Order, reconciliation, Emergency Close, System Pause wiring. This is where FR-023's hard isolation and the credential-handoff §5 procedure actually get exercised.

This mirrors Phase 5's decision record almost exactly and is proposed here for the user to confirm or override, not assumed.

## Explicitly Proposed Out of Scope for Phase 6

- **Any Binance Production credential** — per [credential-handoff.md](../phase-0/credential-handoff.md) §6, that key does not exist until Gate 7 acceptance and a fixed VPS IP assignment (Phase 8). Nothing in Phase 6 should ever construct, request, or reference a Production key.
- **Real money of any kind** — Testnet only, by definition of this phase.
- **The AI/ANALYSIS path** (Strategy Contract, Market Confirmation, Candidate Trade) — untouched, per Gate 5's risk acceptance. The Risk Engine only ever needs to handle `EXECUTION_SIGNAL`-originated Trade Intents for now.
- **Phase 7's 24h end-to-end Testnet soak and full acceptance matrix** — Phase 6's own Gate is narrower ("Golden/Testnet Gate 6" per the Implementation Plan table); the full soak is explicitly Phase 7's job.
- **P4-MAJOR-001** — not this phase's problem to fix, just not to forget.

## Open Questions Before Implementation Planning (several are explicitly Gate-6-blocking in system-spec.md itself)

- **TBD-003 / CS-BN-004** ([system-spec.md](../phase-0/system-spec.md) line 379, [api-contract-inventory.md](../phase-0/api-contract-inventory.md) line 79): the exact Position ROE-to-stop-price formula, including fees, funding, maintenance margin, and slippage buffer — explicitly named "Blocking Gate 6" in system-spec.md. This needs a resolved answer before Risk Engine sizing logic can be considered done, not just before Gate 6 is called.
- **CS-BN-001** (account/position endpoint field shape) and **CS-BN-002** (conditional `algoOrder` semantics in One-way mode) — both explicitly require a live Testnet contract spike (hitting the real Testnet API to see actual response shapes) before the Execution Gateway's schema adapter can be written with confidence, per api-contract-inventory.md's own closing note: "A breaking contract or failed spike makes the Gate `NOT_READY`; no compatibility guess or Production fallback."
- **CS-BN-005** (partial-fill protection sequencing) — needs a deliberately-triggered partial-fill scenario on Testnet to observe real behavior, not assumed from docs.
- Does the user want the proposed Slice 1/Slice 2 split above, or a different order/grouping?
- `position_snapshot`/`exchange_order`/`protection_order`/`fill`/`system_control_state` all currently have only one-line entity-overview descriptions in logical-data-model.md, not full field-level schemas the way `trade_intent`/`risk_decision`/`order_lifecycle` already do (§3.5–3.6) — these need to be designed during this phase's own planning, the same way `market_snapshot`'s schema was designed during Phase 5 Slice 1 rather than assumed upfront. Worth noting: `market_snapshot` itself was implemented in Phase 5 without ever getting a dedicated logical-data-model.md subsection either — this doc should probably be kept current going forward, or the gap will keep repeating.
- Retention period for trade/risk/order/fill/audit data is an explicit TBD (system-spec.md §6, line 241), deferred to "legal/financial review before Gate 8" — not Phase 6's problem to resolve, but worth knowing it stays unresolved through this phase too.

## Current Decision

Phase 6 kickoff scope is drafted above, per the user's authorization to begin planning (2026-09-14). **Slice order confirmed by the user (2026-09-15): Risk Engine first (Slice 1), Execution Gateway after (Slice 2+)** — no Binance Testnet credential is touched until Slice 1 is fully verified. No branch created, no code written yet. Next step: detailed Slice 1 planning (trigger point, data flow, golden/negative fixture design) — see the Slice 1 planning note this triggers.
