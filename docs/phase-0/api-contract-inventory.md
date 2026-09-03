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

## 6. Contract Drift Policy

- Store adapter contract version and official-doc snapshot date in Phase reports.
- Recheck endpoint paths/fields when dependency/API version changes or provider announces deprecation.
- Unknown fields are tolerated only in raw provider payload; mapped internal schema rejects ambiguous semantic changes.
- A breaking contract or failed spike makes the Gate `NOT_READY`; no compatibility guess or Production fallback.
