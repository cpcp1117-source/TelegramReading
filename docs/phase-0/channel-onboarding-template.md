# Channel Onboarding Template

Use one copy per Telegram Source Channel. Do not include login codes, phone numbers, invite tokens, API secrets, paid-member identifiers or unredacted personal data.

## A. Channel Identity

| Field | Value |
|---|---|
| Internal name |  |
| Telegram display title |  |
| Public username (if public) |  |
| Private channel | Yes / No |
| Collector Account already joined | Yes / No |
| Numeric channel ID | Filled by Phase 2 dialog listing |
| Channel type | `ANALYSIS` / `EXECUTION_SIGNAL` |
| Business owner / confirmer | User |

For a private channel, do not paste the invite URL. Join it through the official Telegram client, then let the Phase 2 read-only dialog listing resolve the numeric identity.

## B. Authorization and Content Policy

| Scope | Status | Evidence Reference | Validity / Revocation Notes |
|---|---|---|---|
| Account may access content | `UNKNOWN/PENDING/GRANTED/REVOKED` |  |  |
| Automated collection/monitoring | `UNKNOWN/PENDING/GRANTED/REVOKED` |  |  |
| AI processing | `UNKNOWN/PENDING/GRANTED/REVOKED` |  |  |
| Image/media storage | `UNKNOWN/PENDING/GRANTED/REVOKED` |  |  |

- Content owner terms / restrictions:
- Required retention shorter than 7 days:
- Required deletion process:
- Legal/platform review notes:

Any `UNKNOWN/PENDING/REVOKED` scope must remain disabled for that processing path.

## C. Market and Signal Policy

| Field | Value |
|---|---|
| Allowed USDⓈ-M symbols |  |
| Explicitly prohibited symbols |  |
| Message languages | Traditional Chinese / Simplified Chinese / English / Other |
| Supported content | Text / Caption / Image |
| Expected signal age | Default global hard max 60 sec |
| Max receive lag | Default global hard max 10 sec |
| Max entry deviation | Default global hard max 50 bps |
| Raw retention | Default 7 days or shorter |

### EXECUTION_SIGNAL Behavior

- Typical long words:
- Typical short words:
- Typical cancel/no-trade words:
- Entry styles: market / exact limit / range
- SL styles:
- TP styles:
- Follow-up linking pattern: reply / signal ID / message link / other
- Does author post leverage? If yes, system still fixes 5x.
- Missing SL behavior: `DEFAULT_ROE_30`.
- Missing TP behavior: no synthesized TP.

### ANALYSIS Behavior

- Typical thesis structure:
- Mentioned timeframes:
- Conditions that author uses:
- Invalidation language:
- Draft Strategy Contract owner:
- Human approval expiry requirement:

## D. Representative Fixtures

Minimum 20 samples per channel before Gate 3. Use synthetic/anonymized content unless explicit storage/use permission exists.

| Fixture ID | Type | Scenario | Expected Classification | Expected Fields / Result | Sensitive Data Removed |
|---|---|---|---|---|---|
| CH-001 | Text | Clear LONG |  |  | Yes |
| CH-002 | Text | Clear SHORT |  |  | Yes |
| CH-003 | Text | Negated LONG |  | No Trade Intent | Yes |
| CH-004 | Text | Cancel signal |  | Cancel only | Yes |
| CH-005 | Text | Symbol missing |  | `INCOMPLETE` | Yes |
| CH-006 | Text | Side missing |  | `INCOMPLETE` | Yes |
| CH-007 | Text | SL missing |  | `DEFAULT_ROE_30` | Yes |
| CH-008 | Text | Invalid/wrong-side SL |  | Reject | Yes |
| CH-009 | Text | TP missing |  | No synthesized TP | Yes |
| CH-010 | Text | Market entry |  |  | Yes |
| CH-011 | Text | Limit entry |  |  | Yes |
| CH-012 | Text | Entry range |  |  | Yes |
| CH-013 | Reply | Move SL |  | Linked update | Yes |
| CH-014 | Reply | Partial TP |  | Linked update | Yes |
| CH-015 | Text | Ambiguous follow-up |  | Manual review | Yes |
| CH-016 | Edit | Direction changed |  | New revision | Yes |
| CH-017 | Duplicate | Replayed message |  | No duplicate effect | Yes |
| CH-018 | Stale | Old valid-looking signal |  | Expired/reject | Yes |
| CH-019 | Image | Signal in chart/image |  | Manual review only | Yes |
| CH-020 | Analysis | Conditional thesis |  | Thesis / WAIT | Yes |

## E. Onboarding Gate Decision

| Check | Result |
|---|---|
| Identity resolved by numeric channel ID | Pass / Fail |
| Required authorization scopes granted | Pass / Fail |
| Symbol allowlist non-empty | Pass / Fail |
| 20 fixtures reviewed | Pass / Fail |
| Critical parser false positive = 0 | Pass / Fail |
| Retention configured | Pass / Fail |
| User acceptance recorded | Pass / Fail |

**Decision:** `MONITOR_ONLY / ENABLED / PAUSED / REJECTED`

**Acceptance Owner:** User

**Acceptance Date:**
