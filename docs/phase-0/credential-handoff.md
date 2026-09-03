# Credential Handoff Procedure

## 1. Universal Rules

- Never paste secrets into chat, issues, screenshots, Markdown, source code, fixtures, test reports or Git.
- Repository keeps only names/placeholders such as `.env.example`; example values must be empty.
- User enters phone, login code and 2FA directly into the local terminal during Telegram authorization.
- Services receive only their own secrets through external runtime mounts.
- Any credential accidentally disclosed is treated as compromised and immediately revoked/rotated.
- Logs may show a non-reversible fingerprint or last four characters only when needed for environment verification.

## 2. Credential by Phase

| Phase | Credential Introduced | Holder | Must Not Access |
|---|---|---|---|
| 0 | None | None | All runtime systems |
| 1 | Local database test password generated for offline environment | PostgreSQL/app test config | External providers |
| 2 | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, Telethon session | Collector only | Orchestrator、Bot、Execution |
| 4 | `CONTROL_BOT_TOKEN`, allowlisted numeric user IDs | Control Bot only | Collector、Execution |
| 5 | `OPENAI_API_KEY` only after authorization | Orchestrator only | Collector、Control、Execution |
| 6 | Binance Testnet key/secret | Execution Gateway only | Collector、Orchestrator、Control |
| 8 | Binance Production subaccount key/secret | Production Execution Gateway only | All other services/environments |

## 3. Telegram User Authorization

1. Create a dedicated Collector Account and join Source Channels through the official Telegram app.
2. Create an application at `my.telegram.org` and obtain own `api_id/api_hash`.
3. User places `api_id/api_hash` in a local secret source outside the repository.
4. First collector login prompts in terminal for phone, Telegram code and optional 2FA.
5. Generated session is stored in an encrypted volume outside the repository.
6. Only collector service identity receives read access to the session.
7. Backup of the session, if any, is encrypted and access logged; otherwise reauthorization is preferred.

Never send the login code or 2FA password to the assistant. Telegram documents that the authorized client key acts with the user's identity; session exposure is therefore treated as account credential exposure.

## 4. Control Bot

1. User creates a dedicated bot with `@BotFather`.
2. Store the Bot Token outside the repository and mount only into `control-bot`.
3. Store the user's numeric Telegram user ID as allowlist config; usernames are not authorization identities.
4. Gate 4 verifies unauthorized users are rejected and high-risk commands use short-lived confirmation nonces.

## 5. Binance Testnet

1. Create a Testnet-only key when Phase 6 starts.
2. Set environment explicitly to `TESTNET`; permitted hosts are only the official Testnet REST/WebSocket hosts.
3. Mount key/secret only into Testnet Execution Gateway.
4. Validation output includes environment, endpoint, account alias/fingerprint, permission result—never the full key.
5. Testnet config must not contain any Production endpoint or credential fallback.

## 6. Binance Production Canary

Production credential is created only after Gate 7 acceptance and fixed VPS IP assignment:

- Dedicated low-balance subaccount.
- USDⓈ-M Futures trade permission only.
- Withdrawal disabled.
- Fixed VPS IP allowlist.
- Separate key from Testnet.
- Mounted only into production Execution Gateway.
- Startup state `PAUSED`; read-only preflight precedes any controlled order.

Suggested external paths (deployment convention, not created in Phase 0):

```text
/opt/telegram-trader/secrets/collector.env
/opt/telegram-trader/secrets/telegram.session
/opt/telegram-trader/secrets/control-bot.env
/opt/telegram-trader/secrets/orchestrator-ai.env
/opt/telegram-trader/secrets/binance-testnet.env
/opt/telegram-trader/secrets/binance-production.env
```

Files must be owned by the specific service/deployment account with least read permission. The implementation must not print their contents during diagnostics.

## 7. Rotation and Incident Procedure

| Secret | Detection / Incident | Immediate Action | Recovery Gate |
|---|---|---|---|
| Telegram session/API hash | Session appears in Git/log/chat or unknown authorization | Stop Collector; revoke session/app credential as applicable; rotate/relogin | Re-run Phase 2 security/reconciliation checks |
| Bot Token | Unauthorized bot behavior or disclosure | Revoke via BotFather; pause commands; issue new token | Re-run Gate 4 auth tests |
| AI key | Disclosure or unexpected usage | Revoke key; disable AI processing; review sent content | Re-run Gate 5 provider/security tests |
| Testnet key | Disclosure | Revoke/reissue; no Production impact | Re-run affected Gate 6 tests |
| Production key | Any suspected exposure | Emergency pause; revoke key; inspect/cancel/close positions using official interface; investigate | Full security review and Gate 8 re-acceptance |
| Database password | Exposure | Stop affected services; rotate; audit access; restore if tampering suspected | Reconciliation and audit integrity checks |

## 8. Evidence Without Disclosure

Gate reports may include:

- secret variable name, not value;
- source type (`mounted file`, `runtime secret`, `interactive login`);
- file permission result;
- key permission result;
- fingerprint/last four characters;
- rotation date and owner role;
- scan finding count.

Gate reports must never include full environment dumps, session strings, authorization codes, request signatures or HTTP Authorization headers.
