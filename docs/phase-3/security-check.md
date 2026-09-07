# Phase 3 Security Check

- **Revision:** `9039cac9f5fdb1dfbaea68a26780bf4f96ccc813`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Date:** 2026-09-08
- **Verdict:** PASS
- **External Secrets Introduced:** none (Phase 3 introduces no new credential type)

## Checks

| Control | Evidence | Result |
|---|---|---|
| Repository secret scan | `uv run python scripts/secret_scan.py --root .` → 85 files, 0 findings | PASS |
| `channel_policies` table contains no credentials | schema review: all columns are business config (labels, authorization enum strings, symbol lists, retention days, JSONB); no session/token/key columns | PASS |
| Local `.env` credential loading | [telegram-bootstrap.ps1](../../scripts/telegram-bootstrap.ps1) reads `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` from a local, git-ignored `.env` if present, else falls back to interactive `Read-Host`; the value is never echoed or logged | PASS |
| Database credential handling unchanged | `POSTGRES_PASSWORD`/`APP_DATABASE_PASSWORD` still entered interactively per Phase 1/2; no change this phase | PASS |
| Invalid-state rejection at the database layer | 8 new `CHECK` constraints on `channel_policies` (`channel_type`, all four authorization columns, `symbol_scope_mode`, `gate_decision`, `raw_retention_days`) reject out-of-enum values before they can reach application logic | PASS |
| Least-privilege unaffected | collector container's `read_only`/`cap_drop: ALL`/`no-new-privileges` settings ([compose.yaml](../../compose.yaml)) are unchanged; the new table adds no new filesystem or network surface | PASS |
| Fail-closed on misconfiguration | `resolve_effective_targets` raises `ChannelPolicyError` (non-zero exit) if every configured target loses authorization, rather than silently running with nothing collected or falling back to an insecure default | PASS |
| No new external egress | `channel_policies` is read via the existing internal-only PostgreSQL connection; no new network destination introduced | PASS |

## Manual Review

Reviewed `channel_policy.py`, the new migration, and both onboarding markdown files for any credential-shaped string (API hash patterns, session file paths, phone numbers, tokens). None found — every value is business/authorization metadata already discussed openly with the user in chat (channel IDs, topic IDs, symbol lists, authorization status words).

## Not Covered By This Check

This document covers only what changed in Phase 3 (the Channel Policy table and its enforcement code). It does not re-evaluate the Phase 2 collector's own session/credential handling — see [../phase-2/security-check.md](../phase-2/security-check.md) for that, which remains valid and unaffected by this phase's changes.
