# Phase 6 Known Issues

Phase 6 Gate has not been evaluated yet (Slice 1 -- Risk Engine -- is in
progress; see [phase-kickoff.md](phase-kickoff.md)). This file tracks real
engineering judgment calls made building Slice 1, none of which were
resolved by an existing spec value -- system-spec.md leaves TBD-003 and
several contract questions explicitly open, and account/market data
sourcing has no precedent before this phase.

## Slice 1 Scope and Limitations (by design, not oversight)

| ID | Item | Detail |
|---|---|---|
| P6-LIMIT-001 | `AccountState` (equity, open positions, daily realized loss) is a Slice-1 configuration/self-tracking approximation, not real Binance account data | No Execution Gateway exists yet to query a real account (Slice 2's job). `default_starting_account_state` always starts a run from a clean slate (zero open positions, zero margin used, zero daily loss); within one run, approved intents accumulate against each other (so a batch of several signals in one run doesn't each pretend the account is empty), but that state is discarded between runs. `equity_usdt` comes from the required `RISK_EQUITY_BASELINE_USDT` config value, not a real balance query. BR-010's "existing manual positions also count" cannot actually be satisfied until Slice 2. |
| P6-LIMIT-002 | TBD-003 (system-spec.md, explicitly "Blocking Gate 6"): the ROE-to-stop-price formula is the baseline leverage-only calculation, no fees/funding/maintenance margin/slippage buffer | `risk_engine.compute_roe_stop_price`: `entry * (1 - roe_pct/leverage)` for LONG, `entry * (1 + roe_pct/leverage)` for SHORT. Documented in the function's own docstring as incomplete, not silently assumed precise. Needs the CS-BN-004 Testnet contract spike (Slice 2) to refine. |
| P6-LIMIT-003 | BR-006's price-deviation leg (50bps) is not implemented | Only the source-age (60s) and receive-lag (10s) freshness checks are enforced. No reference price is captured at signal-receive time to deviate from -- fabricating one against the current mark price would compare two different points in time in an unprincipled way. Left unimplemented rather than guessed. |
| P6-LIMIT-004 | BR-006's freshness window (60s source age) will reject almost every real signal under the current manual-approval workflow | The 60-second window is measured from the signal's *own* Telegram message `source_date`, not from Control Bot approval time. Since BR-002 requires human approval before Gate 8 and a human reasonably takes longer than 60 seconds to see a notification and tap Approve, most real approvals will land well outside this window and get rejected `SOURCE_TOO_OLD`. This is treated as **correct, expected behavior** (the Risk Gate doing its job), not a bug to work around -- the alternative would be loosening a spec-defined hard limit without the user's authorization. Worth watching once this runs live: if every real signal is rejected for staleness, that is real information about how this system needs to evolve (faster approval, or a distinct staleness rule for the manual-approval path), not a reason to quietly relax the check. |
| P6-LIMIT-005 | Sizing (BR-009) takes the **minimum** of three independently-computed quantities (risk-based, single-trade margin cap, remaining total-margin headroom) rather than rejecting outright when one cap binds | Judgment call: FR-016 says the Risk Gate must "計算/限制" (calculate/**limit**) position size, which reads as capping, not refusing a trade a smaller size would make acceptable. Not spelled out explicitly in system-spec.md -- flagged here for the user to confirm or override during Gate 6 review. |
| P6-LIMIT-006 | `trade_intent.status` is written once, fully resolved (`RISK_APPROVED`/`RISK_REJECTED`), never literally `CREATED` | The logical state machine (logical-data-model.md §4) describes `CREATED -> RISK_APPROVED/RISK_REJECTED` as if there's a gap between creation and evaluation. In this slice's implementation, evaluation and creation happen atomically in the same transaction (the row is append-only, so there is no later UPDATE to transition it) -- `CREATED` is accepted by the schema's CHECK constraint for a future slice that might need a real two-phase flow, but Slice 1 never writes it. |
| P6-LIMIT-007 | `risk_decision.expires_at` is recorded (`RISK_DECISION_EXPIRY_SECONDS`, default 300s) but not yet enforced by anything | Same "recorded now, enforced by a future consumer" pattern as `normalized_signals.expires_at` was in Phase 4 before the Control Bot's `expires_at` fix gave it a reader. Slice 2 (Execution Gateway) is the natural place to check it before submitting an order. |

## Found While Building Slice 1

| ID | Item | Detail |
|---|---|---|
| P6-NOTICE-001 | The `app` service's `compose.yaml` environment block was missing `OPENAI_API_KEY`/`BINANCE_*`/`THESIS_*` passthrough entirely | Likely the real reason Thesis extraction (Phase 5 Slice 2a) was never run against real data even after being paused, not just the AI-path pause itself -- `docker compose run --rm app python scripts/extract_theses.py` would have failed closed on a missing `OPENAI_API_KEY` even if attempted, since nothing passed it through from `.env`. Fixed as part of this phase's own script (`evaluate_trade_intents.py` needs `RISK_EQUITY_BASELINE_USDT` passed through the same way) -- the `app` service's environment block now explicitly passes through all of `BINANCE_*`/`OPENAI_*`/`THESIS_*`/`RISK_*`, matching `.env.example`. |

## Open Questions Still Blocking a Clean Gate 6 (unchanged from phase-kickoff.md)

TBD-003 (risk math precision), and the three Binance Testnet contract spikes (CS-BN-001/002/005) all remain genuinely open -- they need a live Testnet API hit (Slice 2), not something Slice 1's pure-logic scope could resolve. See [phase-kickoff.md](phase-kickoff.md)'s Open Questions section.
