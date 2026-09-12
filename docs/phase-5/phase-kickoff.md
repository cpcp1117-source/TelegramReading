# Phase 5 Kickoff — Authorized AI / Public Market Data

## Status

- Phase: `Phase 5 — AI analysis/market confirmation` (per [system-spec.md](../phase-0/system-spec.md) §14 Implementation Plan, row 5; capability matrix label "Authorized AI/public market data")
- Status: `KICKOFF`
- Gate 5: `NOT EVALUATED`
- Baseline: Phase 4, accepted `READY` (risk-accepted) on 2026-09-12 (see [../phase-4/gate-4-checklist.md](../phase-4/gate-4-checklist.md))
- Authorized by: User, 2026-09-12
- Active branch: not yet created

## Inherited Items From Prior Phases

- **P3-MAJOR-004** (AI/media authorization enforcement) was reclassified as structurally deferred in Phase 3, converting into a **mandatory Phase 5 Definition-of-Done item**: this phase's own Gate must include an explicit check that `ai_authorization`/`media_authorization` are enforced by code before any AI-processing or media-storage path runs. This is not optional scope — Gate 5 cannot be `READY` without it.
- **P4-MAJOR-001** (edited-message/supersede lifecycle linking) was risk-accepted in Phase 4, not resolved. It remains open, carried forward as a mandatory item to close before Production Gate 8 (or sooner if either onboarded channel is ever observed to produce an edited message). Phase 5 does not need to touch it, but must not forget it.
- Phase 2's 5 originally risk-accepted gaps (24h soak, restart no-loss, live edit-version retention, full reconciliation, combined coverage) remain open — see [../phase-2/gate-2-checklist.md](../phase-2/gate-2-checklist.md).

## Two Scope Decisions Already Made With The User

1. **AI provider: OpenAI API.** The credential is named `OPENAI_API_KEY`, matching the naming already on record in [credential-handoff.md](../phase-0/credential-handoff.md) §2 row 5 — this resolves **TBD-005** (system-spec.md's "AI provider/model/schema version and data retention terms" blocker).
2. **Slice order: market data / dynamic symbol resolution first, AI/ANALYSIS path second.** These are two independent capabilities bundled under one phase label ("Authorized AI/public market data") in the capability matrix, but they have no dependency on each other and very different credential/authorization surfaces:
   - **Slice 1 (this slice): Binance public market data.** No AI credential needed. Closes a concrete, already-visible Phase 4 gap: `@followgerry`'s dynamic-scope symbols (`BINANCE_USDM_ACTIVE_PERPETUAL`) currently always resolve to `PENDING_MARKET_DATA` (Phase 4's `resolve_symbol` has no exchange data to check against). This slice gives it real `exchangeInfo` data to resolve against, so real signals can finally reach `VALIDATED`.
   - **Slice 2+ (later): AI/ANALYSIS path.** Thesis extraction from `bonnie-blockchain` (the one `ANALYSIS`-type channel), Strategy Contract, Market Confirmation, Candidate Trade. Needs the `OPENAI_API_KEY` credential, the P3-MAJOR-004 authorization-enforcement DoD item, and **TBD-004** (Strategy Contract rules per channel) — none of which block Slice 1.

## Objective — Slice 1 (Market Data)

Per [system-spec.md](../phase-0/system-spec.md) FR-002 (dynamic symbol scope must resolve against "fresh/versioned exchangeInfo"), FR-017 (Execution Gateway symbol filters — read-only precursor here, no execution yet), and Data Invariant #2 in [logical-data-model.md](../phase-0/logical-data-model.md) ("Dynamic scope requires a versioned symbol snapshot/fixture and unique eligible mapping; it is never an unrestricted free-text symbol"):

1. A Binance public-market-data client — no API key required for `GET /fapi/v1/exchangeInfo` or public market/mark-price data (per [api-contract-inventory.md](../phase-0/api-contract-inventory.md), these are the two Phase 5-tagged endpoints).
2. A persisted, versioned snapshot of exchange symbol metadata (the `market_snapshot` entity per [logical-data-model.md](../phase-0/logical-data-model.md) §1 — note this entity has no field-level schema yet; that gets defined during this slice's planning).
3. Wiring `normalization.py`'s `resolve_symbol()` so `BINANCE_USDM_ACTIVE_PERPETUAL` mode actually checks a bare/aliased asset against the latest snapshot's active USDⓈ-M perpetuals, instead of unconditionally returning `PENDING_MARKET_DATA`.
4. Freshness/staleness enforcement (BR-006's 60s source age / 10s receive lag / 50bps deviation limits are execution-time concerns for Phase 6+, but BR-012's "fail closed on stale dependency" and NFR-011's liveness/lag metrics apply here too: a stale snapshot must not be treated as valid for resolution).
5. Re-running `normalize_content.py` under a bumped normalizer version against the real backlog once this lands, to see how many of `@followgerry`'s 172 real signals actually reach `VALIDATED` for the first time.

## Explicitly Out of Scope for Slice 1

- No AI/LLM call of any kind — no `OPENAI_API_KEY` is touched in this slice.
- No Binance **trading** credential of any kind — public market data only, no key needed at all (Binance Testnet/Production keys are Phase 6/8 per credential-handoff.md).
- No Thesis, Strategy Contract, Market Confirmation, or Candidate Trade — Slice 2+.
- No change to BR-006's execution-time freshness gates (those apply when an order is about to be placed, Phase 6+) — this slice only needs freshness for symbol-metadata *resolution*, a narrower concern.
- No closing of P3-MAJOR-004 — that's Slice 2+'s job, since it's specifically about *AI*/media authorization, not market data.

## Open Questions Before Implementation Planning

- Exact `market_snapshot` field-level schema (no prior art in logical-data-model.md beyond the one-line entity-overview row) — needs to be designed during Slice 1's own planning, not assumed here.
- Which Binance market-data endpoint(s) beyond `exchangeInfo` are actually needed for Slice 1 — api-contract-inventory.md flags "mark price / market streams" as Phase 5 with "exact chosen stream TBD Phase 5." Slice 1's own planning should resolve this narrowly: is *symbol validity* (exchangeInfo alone) sufficient, or does resolving `BINANCE_USDM_ACTIVE_PERPETUAL` also require live price data at this stage? (Current read: BR-006 price-deviation checks are execution-time, so exchangeInfo alone should be sufficient for Slice 1's resolution purpose — to be confirmed during planning.)
- How often to refresh the snapshot, and what "stale" means for resolution purposes (distinct from BR-006's order-time freshness numbers).

## Current Decision

Phase 5 kickoff scope is drafted above. AI provider (OpenAI) and slice order (market data first) are confirmed by the user. No branch created, no code written yet — next step is detailed Explore-agent research and planning specifically for Slice 1.
