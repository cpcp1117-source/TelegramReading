# Channel Onboarding Record — Monster-貨幣宇宙中心

## 1. Record Status

- **Record ID:** `CHANNEL-FOLLOWGERRY-001`
- **Record Version:** v0.1
- **Recorded Date:** 2026-09-03
- **Current Status:** `DRAFT`
- **Planned Phase 2 Status:** `MONITOR_ONLY`
- **Source of User Decisions:** User-provided onboarding response on 2026-09-03
- **Runtime Enablement:** Not available in Phase 0
- **Phase 0 Initial Scope:** Sole Source Channel, confirmed by user on 2026-09-03

## 2. Channel Identity

| Field | Value | Evidence / Note |
|---|---|---|
| Internal name | `monster_currency_universe` | Stable project label |
| Telegram display title | `Monster-貨幣宇宙中心` | User input and public-page title agree |
| Public username | `@followgerry` | Canonicalized from user-provided public URL |
| Public URL | `https://t.me/followgerry` | Public source; no invite token |
| Private channel | No | Public page returned HTTP 200 on 2026-09-03 |
| Collector Account already joined | Not required for public-page identity; Phase 2 dialog access TBD | MTProto access is not tested in Phase 0 |
| Numeric channel ID | TBD in Phase 2 dialog listing | Title/username are not runtime authorization identity |
| Channel type | `EXECUTION_SIGNAL` | Confirmed by user |
| Business owner / confirmer | User | Acceptance Owner |

Public-page verification confirmed the displayed title only. It does not prove message completeness, MTProto access, content ownership, or permission scope.

## 3. Authorization and Content Policy

| Scope | Status | Evidence Reference | Validity / Revocation Notes |
|---|---|---|---|
| Account may access content | `GRANTED` | User declaration, 2026-09-03 | Reconfirm if access changes |
| Automated collection/monitoring | `GRANTED` | User declaration, 2026-09-03 | User owns responsibility for permission basis |
| AI processing | `GRANTED` | User declaration, 2026-09-03 | AI key still forbidden before Phase 5 |
| Image/media storage | `GRANTED` | User declaration, 2026-09-03 | Default raw retention remains 7 days |

Authorization records enable later Gate testing only. They do not authorize credentials or runtime processing before the relevant accepted Phase.

## 4. Market and Signal Policy

| Field | Value |
|---|---|
| Symbol scope mode | `BINANCE_USDM_ACTIVE_PERPETUAL` |
| Static symbol allowlist | Not used in this mode |
| Explicitly prohibited symbols | None supplied; TBD before Gate 3 if needed |
| Message languages | `zh-TW`, `en` |
| Supported content | `TEXT`, `CAPTION`, `IMAGE` |
| Expected signal age | Global hard maximum 60 seconds |
| Max receive lag | Global hard maximum 10 seconds |
| Max entry deviation | Global hard maximum 50 bps |
| Raw retention | Default 7 days |

### Dynamic Symbol Resolution Contract

1. Parser may extract a source alias such as `CHIP` only from the message evidence.
2. A versioned Binance `exchangeInfo` snapshot must map the alias uniquely to a symbol where `quoteAsset=USDT`, `contractType=PERPETUAL`, and `status=TRADING`.
3. The canonical symbol and snapshot identity must be recorded with the decision.
4. Zero matches, multiple matches, stale/missing snapshot, prohibited symbol, or unsupported contract produces `INCOMPLETE/MANUAL_REVIEW` and no Trade Intent.
5. The word `小` may support direction language such as `小多`, but must never be converted into quantity or a reduced risk budget. Quantity remains exclusively owned by the Risk Engine.

On 2026-09-03, the public Binance USDⓈ-M `exchangeInfo` response listed `CHIPUSDT` as `TRADING`, `PERPETUAL`, base asset `CHIP`, quote asset `USDT`. This is time-bound discovery evidence, not a permanent eligibility guarantee.

## 5. Representative Fixture

| Field | Value |
|---|---|
| Fixture ID | `MONSTER-001` |
| Content type | `TEXT` |
| User-provided anonymized content | `#CHIP 市價小多` |
| Expected symbol alias | `CHIP` |
| Expected canonical symbol | `CHIPUSDT`, only if a fresh eligible symbol snapshot confirms it |
| Expected side | `LONG` |
| Expected entry semantics | `MARKET` |
| Expected SL behavior | `DEFAULT_ROE_30`; never invent an authored stop price |
| Expected TP behavior | Missing; never synthesize TP |
| Expected quantity behavior | No quantity inferred from `小` |
| Phase 0 result | Specification fixture only; no parser or order exists |

## 6. Onboarding Gate Status

| Check | Result | Note |
|---|---|---|
| Public identity recorded | PASS | Numeric ID deferred to Phase 2 as designed |
| Channel type recorded | PASS | `EXECUTION_SIGNAL` |
| Authorization statuses recorded | PASS | All four user-declared `GRANTED` |
| Symbol scope explicit | PASS | Dynamic, exchange-validated, fail-closed |
| Gate 0 representative sample | PASS | One anonymized fixture |
| Initial inventory scope | PASS | User confirmed this is the sole Phase 0 channel |
| Gate 3 fixture set | NOT STARTED | Requires at least 20 fixtures before Gate 3 |
| Runtime/parser behavior | NOT TESTED | Prohibited in Phase 0 |

**Current Decision:** `DRAFT` — eligible for Gate 0 scope review only.

## 7. Official References

- Telegram public page: <https://t.me/followgerry>
- Binance USDⓈ-M Exchange Information: <https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information>
