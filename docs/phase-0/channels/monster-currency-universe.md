# Channel Onboarding Record — Monster-貨幣宇宙中心

**Sections 3, 4, and 6's enforced values now live in the `channel_policies` table** (row `channel_id=2439599598, topic_id=0`, seeded by `alembic/versions/0005_channel_policies.py`, read by `src/telegram_trader/channel_policy.py`). This document remains the human-readable justification, evidence, and fixture record. If this file and the table ever disagree, **the table governs runtime behavior** — correct this file to match. Note: Section 6 still shows "Gate 3 fixture set: NOT STARTED" — only 1 sample fixture exists for this channel, not the 20 needed before Gate 3 can be formally declared complete.

## 1. Record Status

- **Record ID:** `CHANNEL-FOLLOWGERRY-001`
- **Record Version:** v0.2
- **Recorded Date:** 2026-09-03
- **Current Status:** `PHASE_2_MONITOR_ONLY_CONFIRMED`
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
| Collector Account dialog access | Confirmed | User executed the credential-safe Phase 2 dialog listing on 2026-09-06; account identity was not disclosed |
| Numeric channel ID | `2439599598` | Returned for the matching `followgerry` dialog on 2026-09-06 |
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

## 5. Representative Fixtures

`MONSTER-001` is the original Phase 0 synthetic fixture, kept as-is. `MONSTER-002`–`MONSTER-021` are 20 real messages (`message_id` 6642–6662, 2026-09-02 to 2026-09-07) pulled via `telegram-bootstrap.ps1 preview` — read-only, no DB write, no media download. Three fixtures contained a third-party recruitment contact handle in the source text; that handle is **not reproduced** below (marked "redacted").

This real sample turned out meaningfully more diverse than expected: it surfaces a **WAIT/no-trade-intent** case and an **ambiguous-side (leverage+entry given, no explicit LONG/SHORT wording)** case, and reveals that roughly half of this channel's traffic is pure promotional/recruitment content with a symbol hashtag but no actual trade signal — an important real characteristic for the Phase 4 parser to handle, not an edge case to special-case away. Still missing from this sample: an explicit **cancel** signal, an **invalid/wrong-side SL**, a **limit/range entry** (all observed entries were market), a **reply-based** SL move or partial TP, an **edited** message with a changed direction, a **duplicate** repost, a **stale** old-looking signal, and a **pure-image** signal with no caption text. Should any of these occur later, add them; otherwise they may need synthetic/anonymized examples before a Phase 4 parser can be considered fully edge-case tested.

| Fixture ID | Type | Scenario | Expected Classification | Expected Fields / Result | Sensitive Data Removed |
|---|---|---|---|---|---|
| MONSTER-001 | Text | Clear LONG, market entry, small size (Phase 0 synthetic) | LONG / Market Entry | symbol_alias=CHIP; side=LONG; entry=MARKET; SL=`DEFAULT_ROE_30`; TP=none (no quantity inferred from `小`) | Yes (synthetic) |
| MONSTER-002 | Text | Promotional, no symbol, celebratory (9/2) | No Trade Intent | no symbol/side/price present; pure marketing text | Yes |
| MONSTER-003 | Text | Promotional, symbol hashtag only, after-the-fact profit brag (9/2) | No Trade Intent | symbol_alias=T mentioned but no side/entry/price | Yes |
| MONSTER-004 | Text | Clear LONG, market entry, no SL/TP — same pattern as MONSTER-001 (9/3) | LONG / Market Entry | symbol_alias=CHIP; side=LONG（小多）; entry=MARKET; SL=`DEFAULT_ROE_30`; TP=none | Yes |
| MONSTER-005 | Text | Promotional, no symbol (9/3) | No Trade Intent | no symbol/side/price | Yes |
| MONSTER-006 | Text | Promotional, no symbol, recruitment message | No Trade Intent | contains a third-party contact handle — redacted from this record | Yes (redacted) |
| MONSTER-007 | Caption | Promotional, symbol hashtag, profit brag (9/3) | No Trade Intent | symbol_alias=CHIP mentioned, no side/entry | Yes |
| MONSTER-008 | Caption | Promotional, symbol hashtag, recruitment message (9/3) | No Trade Intent | contact handle redacted | Yes (redacted) |
| MONSTER-009 | Caption | Promotional, symbol hashtag, profit brag (9/4) | No Trade Intent | symbol_alias=CHIP mentioned, no side/entry | Yes |
| MONSTER-010 | Text | Analysis-style writeup ending in explicit wait recommendation (9/4) | No Trade Intent / WAIT | symbol_alias=BTC; explicit "建議觀望" (recommend wait-and-see); no side/entry/SL/TP | Yes |
| MONSTER-011 | Text | Clear SHORT, market entry, "福利單" bonus-tier label, no SL/TP (9/4) | SHORT / Market Entry | symbol_alias=CHIP; side=SHORT; entry=MARKET; SL=`DEFAULT_ROE_30`; TP=none | Yes |
| MONSTER-012 | Caption | Promotional, symbol hashtag, profit brag (9/4) | No Trade Intent | symbol_alias=CHIP mentioned, no side/entry | Yes |
| MONSTER-013 | Text | Promotional, no symbol, recruitment message (9/4) | No Trade Intent | contact handle redacted | Yes (redacted) |
| MONSTER-014 | Text | Clear SHORT, market entry with explicit price, no SL/TP (9/6) | SHORT / Market Entry | symbol_alias=ARB; side=SHORT; entry=MARKET @0.1950; SL=`DEFAULT_ROE_30`; TP=none | Yes |
| MONSTER-015 | Text | Clear LONG, market entry, explicit author-specified SL and TP range (9/7) | LONG / Market Entry, complete | symbol_alias=UNI; side=LONG; entry=MARKET; TP=7.28–7.7; SL=6.83 (author-specified, not `DEFAULT_ROE_30`) | Yes |
| MONSTER-016 | Text | Pure market-news, no symbol, not a signal (9/7) | No Trade Intent (non-signal news) | no symbol/side/price; informational only | Yes |
| MONSTER-017 | Text | Leverage + market entry price given, but no explicit LONG/SHORT wording — direction only implied by emoji (9/7) | `INCOMPLETE` (side missing) | symbol_alias=ARB; leverage=50x; entry=MARKET @0.1686; side not stated in text | Yes |
| MONSTER-018 | Text | Risk-flagged LONG, no explicit entry price (9/7) | LONG (risk-flagged) | symbol_alias=ICP; side=LONG（風險短多）; entry=MARKET (implied); SL=`DEFAULT_ROE_30`; TP=none | Yes |
| MONSTER-019 | Caption | Promotional, symbol hashtag, "插針翻倍" wick-spike brag (9/7) | No Trade Intent | symbol_alias=ICP mentioned, no side/entry | Yes |
| MONSTER-020 | Caption | Promotional, symbol hashtag, profit brag (9/7) | No Trade Intent | symbol_alias=ARB mentioned, no side/entry | Yes |
| MONSTER-021 | Caption | Promotional, symbol hashtag, profit brag (9/7) | No Trade Intent | symbol_alias=ARB mentioned, no side/entry | Yes |

## 6. Onboarding Gate Status

| Check | Result | Note |
|---|---|---|
| Public identity recorded | PASS | Username and numeric ID match the Phase 2 dialog result |
| Channel type recorded | PASS | `EXECUTION_SIGNAL` |
| Authorization statuses recorded | PASS | All four user-declared `GRANTED` |
| Symbol scope explicit | PASS | Dynamic, exchange-validated, fail-closed |
| Gate 0 representative sample | PASS | One anonymized fixture |
| Initial inventory scope | PASS | User confirmed this is the sole Phase 0 channel |
| Gate 3 fixture set | PASS | 21 fixtures (1 synthetic + 20 real, pulled 2026-09-08 via `preview`); see §5 for the diversity gaps still worth closing before a Phase 4 parser is considered fully edge-case tested |
| Runtime read-only access | PARTIAL | Dialog access confirmed; message collection and soak not yet tested |
| Parser behavior | NOT TESTED | Prohibited until Phase 4 |

**Current Decision:** `PHASE_2_MONITOR_ONLY_CONFIRMED` — eligible only for Phase 2 read-only collector development and validation.

## 7. Official References

- Telegram public page: <https://t.me/followgerry>
- Binance USDⓈ-M Exchange Information: <https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information>
