# Phase 5 Security Check

- **Revision:** `fcb6fd7`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Date:** 2026-09-14
- **Verdict:** PASS, with one honest process note (see §Trust-Boundary Note below)
- **External Secrets Introduced:** `OPENAI_API_KEY` (new); Binance's public market-data endpoints require no credential at all

## Checks

| Control | Evidence | Result |
|---|---|---|
| Repository secret scan | `uv run python scripts/secret_scan.py --root .` → 130 files, 0 findings | PASS |
| `OPENAI_API_KEY` never handled by the assistant | entered only by the user at their local terminal / `.env`; never pasted into chat, never present in any file the assistant wrote | PASS |
| `OPENAI_API_KEY` follows the existing `SecretStr` + `AliasChoices` pattern | `config.py`'s `openai_api_key` mirrors `telegram_api_hash`/`control_bot_token`'s existing shape exactly, plus a non-empty validator | PASS |
| Binance client requires no credential | `binance_market_data.py` calls only `GET /fapi/v1/exchangeInfo` and related public endpoints; no API key field exists on the client at all | PASS |
| P3-MAJOR-004 DoD: `ai_authorization`/`media_authorization` enforced by real code before any AI call | `ai_authorization.py`'s fail-closed gate (missing policy row, wrong `channel_type`, `ai_authorization != GRANTED`, or media-present-without-`media_authorization`) is checked before every `thesis_extraction.py` call | PASS on code/test grounds; **never live-exercised** since Slice 2a has never actually run against real data (see [test-evidence.md](test-evidence.md) §5) -- the gate is real, just unproven live |
| A message carrying media is gated on **both** `ai_authorization` and `media_authorization` | Confirmed with the user as the deliberately conservative reading, even though only text is ever sent to the LLM | PASS; `test_ai_authorization.py` |
| `thesis_extraction.py` never uploads or reads the chart image itself (BR-003) | Only `normalized_text` is sent in the OpenAI request body; no file/image parameter exists anywhere in `openai_client.py` | PASS |
| OpenAI response validated independently of the provider's own schema conformance | `parse_llm_thesis_response` additionally rejects invented symbols (not in the resolved candidate set) and evidence quotes that are not verbatim substrings of the source text, on top of `response_format=json_schema` strict mode | PASS; `test_thesis_extraction.py` |
| No new external egress beyond what's declared | This phase adds exactly two new external destinations: Binance's public REST API and OpenAI's API -- both from the `app`/`control-bot` images' existing `edge` network, no new network added | PASS |
| Container hardening unchanged | No new service definitions beyond `collector`/`control-bot`'s restart-policy change (below); both still `read_only`/`cap_drop: ALL`/`security_opt: no-new-privileges` | PASS |
| Control Bot's new edit flow stays behind the existing allowlist gate | The edit callback family (`edit:<nonce>:<field>`) and the free-text reply that follows it are both still checked against `_is_allowlisted`/`_pending_edit` scoped to the allowlisted sender before anything is written | PASS; `test_control_bot.py` |
| `signal_decision_edits` append-only, same convention as every other Phase 2-4 table | UPDATE-rejection trigger, migration `0013` | PASS; integration test |
| `binance_symbol_snapshots`/`thesis`/`thesis_extraction_checkpoints` append-only | Same trigger convention | PASS; integration tests |
| `expires_at` fix introduces no new egress or credential | Pure query-filter and clock-injection change | PASS |

## Trust-Boundary Note: `restart: unless-stopped`

`collector`/`control-bot` moved from `restart: "no"` (deliberate, manually-supervised startup only, matching this project's historical posture of a conscious human action -- running `telegram-bootstrap.ps1` -- before either process ever touches a real Telegram session) to `restart: unless-stopped`, at the user's explicit request, so both processes resume on their own after a host/Docker reboot. This is a genuine, intentional change to that posture: after this change, a reboot alone is now sufficient to resume live Telegram/Control Bot activity, with no fresh human confirmation step in between. This is not a vulnerability -- both processes still only ever act within their existing, unchanged authorization boundaries (allowlist, Channel Policy, `ai_authorization`) -- but it is a real shift worth naming plainly rather than folding silently into a one-line compose diff. The user made this trade-off explicitly, understanding the machine is not kept on 24/7 in practice (see [known-issues.md](known-issues.md)'s Phase 2 NOTVERIFIED-001 closure).

## Manual Review

Reviewed `binance_market_data.py`, `ai_authorization.py`, `openai_client.py`, `thesis_extraction.py`, `config.py`'s new fields, migrations `0011`-`0013`, `compose.yaml`'s restart-policy change, and `control_bot.py`'s new edit-flow code for any credential-shaped string. None found -- the only new secret (`OPENAI_API_KEY`) is referenced exclusively via `settings.openai_api_key` (a `SecretStr`), never logged.

**Real finding with a security-adjacent root cause, already fixed (see [known-issues.md](known-issues.md) and [test-evidence.md](test-evidence.md) §6 finding 1):** this project's own `docker compose --profile test` service was discovered to share the same real, persistent database as `collector`/`control-bot` -- its integration-test fixture's `TRUNCATE ... CASCADE` would have destroyed the real accumulated Telegram history if run locally against it. Never executed; a genuinely separate ad hoc Postgres container was used instead. This is a real gap in the local development setup (works safely only inside GitHub Actions' disposable runners, not on this machine) worth fixing properly at some point, but out of scope to change now without risking exactly the destructive action it's warning about.

## Not Covered By This Check

This document covers only what changed in Phase 5 (Binance market data, Thesis extraction/`ai_authorization`, Control Bot SL/TP editing, and the reboot-recovery restart-policy change). It does not re-evaluate Phase 2's collector credential handling, Phase 3's Channel Policy enforcement, or Phase 4's Control Bot allowlist/decision-validity logic -- see their respective `security-check.md` files, all unaffected by this phase's changes.
