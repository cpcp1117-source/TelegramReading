# Telegram / Binance API Contract Inventory

- **Snapshot Date:** 2026-09-03
- **Purpose:** Identify authoritative external contracts and required contract spikes; this is not implementation proof.
- **Rule:** Recheck official documentation and changelogs at the start of the Phase that implements each contract.

## 1. Telegram MTProto

| Contract | Purpose | Auth | Confirmed Behavior / Risk | Phase Verification | Authority |
|---|---|---|---|---|---|
| User Authorization | Associate MTProto auth key with Collector Account | `api_id/api_hash`, phone code, optional 2FA | Authorized calls act with user identity; session is a critical credential | Interactive login without logging code/2FA | [Telegram User Authorization](https://core.telegram.org/api/auth) |
| `messages.getHistory` | Read peer history | User only | Results descending; private/unjoined channel can return `CHANNEL_PRIVATE`; not sufficient for every channel gap | Pagination/access/error fixtures | [messages.getHistory](https://core.telegram.org/method/messages.getHistory) |
| Updates state/difference | Receive updates and recover gaps | Authorized user | Client must track state and fill gaps; encrypted/authorized update handling required | Disconnect, gap, difference recovery | [Working with Updates](https://core.telegram.org/api/updates) |
| API Terms | Usage obligations | Application/user | Own `api_id`, transparency, content/AI terms apply | Authorization/onboarding review | [Telegram API Terms](https://core.telegram.org/api/terms) |
| Content Licensing | Content use and AI restrictions | Human/legal permission | AI/data aggregation restrictions; context-specific consent exception language | Per-channel authorization Gate | [Content Licensing](https://telegram.org/tos/content-licensing) |

### Telethon Adapter Candidate

| Area | Candidate | Status / Spike |
|---|---|---|
| Client library | Telethon | Proposed; pin version only in Phase 2 after adapter contract tests |
| Events | `NewMessage`, `MessageEdited` and related event builders | Verify actual message/edit/reply/media shapes against [Telethon event docs](https://docs.telethon.dev/en/stable/modules/events.html) |
| Session storage | File/String session options | Choose file on encrypted volume after leak/restart tests; do not place in DB/repo |
| Gap recovery | Library catch-up plus explicit official-state semantics | Must prove controlled gap/restart; do not assume event callbacks alone are complete |

## 2. Telegram Bot API

Control Bot uses the separate HTTP Bot API, not the Collector User session.

| Contract Area | Requirement | Phase Verification | Authority |
|---|---|---|---|
| Updates | Polling/webhook choice TBD in Phase 4; only private user commands accepted | Numeric user ID allowlist、duplicate update handling | [Telegram Bot API](https://core.telegram.org/bots/api) |
| Callback queries | Approval/reject/confirmation callbacks carry opaque short-lived IDs, not secrets | Revision/nonce/expiry tests | Same |
| Commands | `/status`, `/signals`, `/positions`, `/pause`, `/resume`, `/close`, `/close_all` | Unauthorized and confirmation tests | Same |

## 3. Binance USDⓈ-M Futures Environments

| Environment | REST Base | WebSocket Base | Credential Policy | Authority |
|---|---|---|---|---|
| Testnet | `https://demo-fapi.binance.com` | `wss://demo-fstream.binance.com` | Testnet-only key; Phase 6–7 | [General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) |
| Production | `https://fapi.binance.com` | Current official USDⓈ-M production stream host | Low-balance subaccount, no withdrawal, fixed IP; Phase 8 only | Same |

No code/config may derive one environment from the other by fallback. Allowed hosts are explicit per environment.

## 4. Binance Public/Account/Trade Contracts

| Method / Stream | Purpose | Security | Important Fields / Rules | Phase |
|---|---|---|---|---|
| `GET /fapi/v1/time` | Server time/skew | Public | Signed requests depend on valid timestamp/recvWindow | 5/6 |
| `GET /fapi/v1/exchangeInfo` | Symbol status、filters、rate limits | Public | `PRICE_FILTER`, quantity/notional filters and `TRADING` status are authoritative; use Decimal | 5/6 |
| Mark price / market streams | Fresh market confirmation and stop reference | Public WS/REST | Track source/receive/resync ages; exact chosen stream TBD Phase 5 | 5 |
| Account/position endpoints | Equity、mode、margin、positions | Signed USER_DATA | Exact V2/V3 endpoint choice requires Phase 6 contract spike | 6 |
| Position mode | Enforce One-way | Signed TRADE | `dualSidePosition=false`; preflight, do not silently change with open positions/orders | 6 |
| Margin type | Enforce Isolated | Signed TRADE | Symbol-level `ISOLATED`; validate response/current state | 6 |
| Initial leverage | Enforce 5x | Signed TRADE | Symbol-level leverage | 6 |
| `POST /fapi/v1/order` | Entry/close order | Signed TRADE | `symbol`, `side`, `type`, `positionSide=BOTH`, quantity/price, unique `newClientOrderId` | 6 |
| Query order | Resolve ambiguous outcome | Signed USER_DATA | Query by order/client ID before resend | 6 |
| `POST /fapi/v1/algoOrder` | Conditional TP/SL/trailing | Signed TRADE | Current official docs identify it for conditional orders; fields include `algoType=CONDITIONAL`, type, stop/close semantics | 6 |
| Query/cancel algo order | Reconcile/cancel Protection Order | Signed USER_DATA/TRADE | Track both local client and exchange algo identities | 6 |
| User Data Stream | Order/account events | API key/listen key or current WS API | Stream may require keepalive/reconnect; use events then REST reconcile | 6/7 |

Authoritative entry points:

- [Trade API / New Order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order)
- [Exchange Information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)
- [User Data Stream](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-api/user-data-streams)

## 5. Required Contract Spikes

| Spike ID | Question | Evidence Required | Blocking Gate |
|---|---|---|---|
| CS-TG-001 | Can selected Telethon version reproduce official update/gap semantics? | Controlled disconnect/gap/recovery trace and source comparison | 2 |
| CS-TG-002 | Exact event shapes for edit/reply/forward/media? | Sanitized fixture captures and adapter tests | 2 |
| CS-BOT-001 | Polling vs webhook for fixed VPS and idempotent callbacks? | Small threat/ops comparison and command contract tests | 4 |
| CS-AI-001 | Provider structured-output contract and retention/data terms? | Official docs/terms, schema failure tests, permission record | 5 |
| CS-BN-001 | Current Account/Position V2/V3 fields and mode semantics? | Testnet responses, schema adapter tests | 6 |
| CS-BN-002 | Exact conditional `algoOrder` close/quantity/workingType behavior in One-way mode? | Testnet create/query/cancel/trigger trace | 6 |
| CS-BN-003 | User Data Stream start/keepalive/reconnect/current event schema? | 24h-relevant reconnect and REST reconciliation trace | 6/7 |
| CS-BN-004 | Position ROE-to-stop formula with fees/funding/maintenance/slippage? | Golden cases reconciled to Testnet position/account data | 6 |
| CS-BN-005 | Partial fill protection sequencing? | Testnet partial fill and protection/emergency scenarios | 6 |

### CS-BN-001 — Resolved 2026-09-25

Captured via `scripts/binance_testnet_spike.py` (read-only signed `GET`, no order placed) against a real Testnet account:

- `GET /fapi/v2/account` and `GET /fapi/v2/positionRisk` both return `positionSide: "BOTH"` on every entry (not `LONG`/`SHORT`) — **confirms the account is in One-way mode**, not Hedge mode. This is a precondition CS-BN-002 needed and is now known, not assumed.
- `account.positions[]` and `positionRisk[]` list all ~740 listed symbols regardless of whether a position is open (flat entries have `positionAmt: "0"`); a real position adapter must filter, not assume the response is pre-filtered to open positions only.
- Field shapes match current official docs field-for-field (`initialMargin`, `maintMargin`, `entryPrice`, `breakEvenPrice`, `leverage`, `isolated`, `notional`, `liquidationPrice`, `marginType`, `adlQuantile`, etc.) — no undocumented or renamed fields found on Testnet at time of spike.

### CS-BN-002 — Closed 2026-09-28 (corrects the 2026-09-25 "substantially resolved" note below)

**The 2026-09-25 conclusion was wrong, and was corrected by an actual placement attempt, not another read-only observation.** Building Slice 2a's `execution_gateway.py`, the very first real attempt to place a `STOP_MARKET` protection order via `POST /fapi/v1/order` (the plain order endpoint) was rejected outright:

```
HTTP 400 code=-4120: Order type not supported for this endpoint. Please use the Algo Order API endpoints instead.
```

`POST /fapi/v1/algoOrder` (`algoType=CONDITIONAL`) — this project's *original* Phase 0 hypothesis, which the 2026-09-25 note below overturned based only on a `GET /fapi/v1/openOrders` observation of an already-placed order — is in fact the only way to place a new `STOP_MARKET`/`TAKE_PROFIT_MARKET` order on USDⓈ-M Futures. The lesson: observing an existing order's *shape* via a read-only query is not the same as exercising the *placement* contract; the read-only spike never actually called the endpoint it was drawing conclusions about. Query is via a **separate** endpoint too: `GET /fapi/v1/algoOrder` (by `algoId` or `clientAlgoId`) — a placed algo order does **not** appear in `GET /fapi/v1/openOrders` at all (confirmed live: a real `STOP_MARKET` algo order with `algoStatus: "NEW"` was invisible to `openOrders`, only visible via `GET /fapi/v1/algoOrder`). `execution_gateway.py`'s `_place_and_confirm_protection` uses `place_algo_order`/`get_algo_order` (see `binance_trading_client.py`) accordingly. Confirmed field semantics unchanged from the earlier note: `workingType: "CONTRACT_PRICE"`, `closePosition: false` with explicit `reduceOnly`+`quantity`, `positionSide: "BOTH"`.

Also confirmed live: `newClientOrderId`'s idempotency (FR-018) only holds while an order is *active* -- once a `MARKET` entry order fills (closing it), Binance allows reusing that same `clientOrderId` for a genuinely new order rather than rejecting/deduplicating it. A retried script run within that fill window can therefore double-submit a real entry. Slice 2a has no defense against this yet beyond "don't manually re-run the script against an intent that already has a lifecycle row" (which `load_eligible_intents` already enforces for *separate* runs) -- the actual gap is a *retry within the same run* racing a fill, which is exactly what happened once during this session's own testing (caught and manually closed out, no code fix yet). Tracked in known-issues.md.

<details>
<summary>Original 2026-09-25 note (superseded above, kept for the record)</summary>

A real BTCUSDT entry placed through Binance's own web UI (0.0693 BTC, 20x, cross) auto-attached an OTOCO stop-loss/take-profit bracket. `GET /fapi/v1/openOrders` (via the spike script) shows both legs as ordinary order objects returned by the standard order-query surface, not a separate algo-order listing:

```json
{ "type": "TAKE_PROFIT_MARKET", "stopPrice": "84800", "reduceOnly": true, "closePosition": false,
  "workingType": "CONTRACT_PRICE", "positionSide": "BOTH", "strategyType": "OTOCO" }
{ "type": "STOP_MARKET", "stopPrice": "82900", "reduceOnly": true, "closePosition": false,
  "workingType": "CONTRACT_PRICE", "positionSide": "BOTH", "strategyType": "OTOCO" }
```

This was read as overturning the original `algoOrder` guess. It didn't: the web UI's OTOCO bracket most likely uses a *different*, unpublished internal mechanism (or the plain endpoint behaves differently for Binance's own first-party client) -- these two legs merely *displaying* alongside regular orders in `openOrders` said nothing about which endpoint a third-party API caller must use to create one, which is the actual CS-BN-002 question.

</details>

**Closed 2026-09-28 — can the web UI's "TP/SL at entry" checkbox be replicated in one API call?** No. Checked official docs (`developers.binance.com`) and the Binance Developer Community forum directly: Binance's `order/list` OTO/OTOCO endpoints exist only for **Spot and Margin** (`Margin Account New OTOCO`, Spot's OTO/OTOCO glossary entries) — there is no USDⓈ-M Futures equivalent. The Futures-side `POST /fapi/v1/algoOrder` (`algoType=CONDITIONAL`) places a standalone conditional order, not one linked to an unfilled entry. The Binance Developer Community's own answer to "How to implement OTOCO(TP/SL) orders using API" ([dev.binance.vision/t/1622](https://dev.binance.vision/t/how-to-implement-otoco-tp-sl-orders-using-api/1622)) states plainly: *"there is no single API endpoint that creates a position with attached TP/SL orders atomically... the web UI's TP/SL checkbox uses Binance's internal OTOCO strategy, but this atomic functionality is not currently exposed through the public REST API."* Confirms: entry-then-verify-fill-then-place-protection (exactly Slice 2a's design, driven by FR-019) is not just the safer choice among options -- for USDⓈ-M Futures, it is the only way to do this through the public API at all.

### CS-BN-005 — Still open

The same entry order filled entirely in one trade (`user_trades[0].qty == origQty`) — no partial fill was observed. Needs a deliberately-undersized-liquidity limit order (or a Testnet scenario known to fragment fills) to actually see partial-fill sequencing.

### CS-BN-004 / TBD-003 — Data gathered 2026-09-25, formula update still pending

Same spike also pulled real Testnet values feeding the ROE-to-stop-price formula:

- `GET /fapi/v1/leverageBracket?symbol=BTCUSDT` → full maintenance-margin-ratio bracket table (e.g. bracket 1: notional 0–50,000 USDT, `maintMarginRatio: 0.004`, `initialLeverage: 125`).
- `GET /fapi/v1/commissionRate?symbol=BTCUSDT` → `makerCommissionRate: 0.0002`, `takerCommissionRate: 0.0004`.
- `GET /fapi/v1/premiumIndex?symbol=BTCUSDT` (public) → `lastFundingRate: 0.0001`, 8h funding interval.

Maintenance margin and fee/funding rate are now real numbers, not assumed. Slippage buffer is a policy choice, not a value the API returns — still needs a decision the same way `RISK_MAX_PRICE_DEVIATION_BPS` was. `risk_engine.compute_roe_stop_price` has not been updated yet; this spike only gathered the inputs the refined formula will need.

## 6. Contract Drift Policy

- Store adapter contract version and official-doc snapshot date in Phase reports.
- Recheck endpoint paths/fields when dependency/API version changes or provider announces deprecation.
- Unknown fields are tolerated only in raw provider payload; mapped internal schema rejects ambiguous semantic changes.
- A breaking contract or failed spike makes the Gate `NOT_READY`; no compatibility guess or Production fallback.
