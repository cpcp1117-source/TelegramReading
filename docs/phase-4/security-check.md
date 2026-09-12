# Phase 4 Security Check

- **Revision:** `3a89cf4`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Date:** 2026-09-11
- **Verdict:** PASS
- **External Secrets Introduced:** `CONTROL_BOT_TOKEN` (new Telegram Bot API token); `CONTROL_BOT_ALLOWLISTED_USER_ID` (not a secret, but a security-relevant configuration value)

## Checks

| Control | Evidence | Result |
|---|---|---|
| Repository secret scan | `uv run python scripts/secret_scan.py --root .` → 110 files, 0 findings | PASS |
| New credential (`CONTROL_BOT_TOKEN`) never handled by the assistant | credential entered only by the user at their local terminal / `.env`; never pasted into chat, never present in any file the assistant wrote | PASS |
| `CONTROL_BOT_TOKEN` follows the existing `SecretStr` + `AliasChoices` pattern | `config.py::RuntimeEnvironment` extension mirrors `telegram_api_hash`'s existing shape exactly; `SecretStr` prevents accidental `repr()`/log exposure | PASS |
| `logging_config.py`'s `SENSITIVE_KEYS` already covers bot tokens | `"bot_token"` was already present before this phase; no change needed, verified by re-reading the constant | PASS |
| Control Bot uses a separate credential from the collector's MTProto user session | established Phase 0 trust boundary ([architecture.md](../phase-0/architecture.md) Trust Boundaries table); Bot Token and user session are stored at different `secrets/telegram/*` paths and cannot be confused at the config level (`control_bot_token` vs `telegram_session_path`) | PASS |
| Authorization enforced before any other logic runs | `_on_message`/`_on_callback` check `sender_id == control_bot_allowlisted_user_id` as the first statement; non-matching senders get no reply at all (not even an error), avoiding giving a probing attacker an oracle | PASS |
| Authorization rejection rate under test | `test_on_message_silently_rejects_unauthorized_sender`, `test_on_callback_silently_rejects_unauthorized_sender` — both assert zero replies, zero DB access attempted | PASS |
| Authorization rejection verified live, not just in unit tests | a real Telegram message from sender `8829571367` (not the allowlisted user) was silently rejected and logged during the live run | PASS |
| High-risk command (`close_all`) requires double confirmation | nonce-gated confirmation button with an expiry window; wrong-nonce and expired-window cases both explicitly tested and rejected with an alert, not silently ignored | PASS |
| Callback authenticity: nonce is server-generated and unguessable | `generate_nonce()` uses `secrets.token_hex` (128 bits); `signal_decision_requests.nonce` is `UNIQUE`-constrained at the database level, so no two requests can collide even under a race | PASS |
| Decision idempotency against Telegram's at-least-once callback delivery | `event_id = sha256(request_id + telegram_callback_query_id)`; `record_decision` checks for an existing event by `event_id` first and returns the original outcome verbatim rather than re-deriving a possibly-different one | PASS; regression test `test_record_decision_idempotent_same_callback_query_id` added after this was caught as a bug in the assistant's own first implementation |
| Decision validity: allowlisted actor, unexpired, current revision (Data Invariant #4 analogue) | `record_decision` re-checks `expires_at` and that the request's `signal_row_id` is still the signal's latest revision, independent of whatever the button's callback data claims | PASS; integration tests for `REJECTED_STALE`/`REJECTED_EXPIRED` |
| All new tables append-only | `reject_normalized_content_update()`, `reject_normalized_signals_update()`, `reject_signal_decision_requests_update()`, `reject_signal_decision_events_update()` triggers, same convention as Phase 2/3 | PASS; UPDATE-rejection tests for each |
| Cascade delete reaches all new tables during retention cleanup | `ON DELETE CASCADE` chains `normalized_content`/`normalized_signals`/`signal_decision_requests`/`signal_decision_events` back to `telegram_message_versions` | PASS; retention-cleanup cascade integration tests |
| No new external egress beyond Telegram | Control Bot's only network destinations are Telegram's Bot API (via Telethon) and the existing internal-only PostgreSQL connection; no Binance or other third-party call exists in this phase's code | PASS |
| Container hardening unchanged | new `control-bot` service in `compose.yaml` reuses the same `read_only`/`cap_drop: ALL`/`security_opt`/`networks: [internal, edge]` block as `collector`, not a looser one | PASS |
| `Decimal` JSONB encoding does not leak precision or type-confuse | `entry_values`/`take_profits` stored as lists of `str(Decimal)`, decoded back via `Decimal(str(x))`; no float round-trip at any point | PASS |
| RUF001 per-file ignore is scoped, not global | `pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]` entry applies only to `control_bot.py` (legitimate Traditional Chinese user-facing strings), not repo-wide | PASS |

## Manual Review

Reviewed `control_bot.py`, `signal_decisions.py`, `config.py`'s new fields, both new migrations, `compose.yaml`'s new service, and `telegram-bootstrap.ps1`'s new `control-bot` branch for any credential-shaped string. None found — the only new secret (`CONTROL_BOT_TOKEN`) is referenced exclusively via `settings.control_bot_token` (a `SecretStr`), never logged, never interpolated into an f-string that reaches a log call.

**Named, accepted trust-boundary note (same shape as Phase 3's):** `control_bot_allowlisted_user_id` is config, not data — anyone who can edit the running container's environment already controls who the bot treats as authorized, the same pre-existing trust boundary as anyone who can edit `channel_policies.gate_decision`. This is not a new exposure.

**Real bug with a security-adjacent root cause, already fixed (see [known-issues.md](known-issues.md) P4-MAJOR-003):** the original callback-data encoding embedded the full `request_id`, which — beyond exceeding Telegram's byte limit — meant the physical primary key of a database row was round-tripped through a user-facing Telegram button, and the decode path had to trust it without independent verification. The fix (resolve by `nonce` alone, unique-constrained, unrelated in value to the primary key) both fixes the crash and removes an unnecessary internal-ID exposure into a third-party (Telegram) system, though no evidence suggests this was ever exploited before the crash-loop was already blocking all functionality.

## Not Covered By This Check

This document covers only what changed in Phase 4 (normalization, signal parsing, Control Bot, and their supporting migrations/config/container changes). It does not re-evaluate Phase 2's collector credential handling or Phase 3's Channel Policy enforcement — see [../phase-2/security-check.md](../phase-2/security-check.md) and [../phase-3/security-check.md](../phase-3/security-check.md), both unaffected by this phase's changes.
