# Phase 3 Security Check

- **Revision:** `eea4f29`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Date:** 2026-09-08
- **Verdict:** PASS
- **External Secrets Introduced:** none (Phase 3 introduces no new credential type)

## Checks

| Control | Evidence | Result |
|---|---|---|
| Repository secret scan | `uv run python scripts/secret_scan.py --root .` → 94 files, 0 findings | PASS |
| Append-only bypass is age-gated, not just flag-gated | Migration 0006's trigger function checks the row's actual age against `channel_policies.raw_retention_days` server-side before allowing a DELETE — a compromised app credential that sets `SET LOCAL app.retention_cleanup='on'` and issues an unscoped DELETE still cannot remove a non-expired row; verified by a dedicated regression test (`test_retention_trigger_rejects_delete_of_non_expired_row`) | PASS |
| `SET LOCAL`, not bare `SET`, for the retention bypass | connection pooling means a bare `SET` would persist the bypass flag onto a reused connection past its transaction; the implementation uses `SET LOCAL` exclusively, scoping the bypass to one transaction | PASS |
| `UPDATE` on `telegram_message_versions` remains unconditionally rejected | migration 0006 only carves out a DELETE exception; no code path needs UPDATE | PASS |
| Retention job is a separate, non-always-running process | `scripts/retention_cleanup.py` is invoked manually/by the host's own scheduler, same category as `scripts/secret_scan.py`; it is not wired into the always-connected collector, limiting its exposure window | PASS |
| `channel_policies` table contains no credentials | schema review: all columns are business config (labels, authorization enum strings, symbol lists, retention days, JSONB); no session/token/key columns | PASS |
| Local `.env` credential loading | [telegram-bootstrap.ps1](../../scripts/telegram-bootstrap.ps1) reads `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` from a local, git-ignored `.env` if present, else falls back to interactive `Read-Host`; the value is never echoed or logged | PASS |
| Database credential handling unchanged | `POSTGRES_PASSWORD`/`APP_DATABASE_PASSWORD` still entered interactively per Phase 1/2; no change this phase | PASS |
| Invalid-state rejection at the database layer | 8 new `CHECK` constraints on `channel_policies` (`channel_type`, all four authorization columns, `symbol_scope_mode`, `gate_decision`, `raw_retention_days`) reject out-of-enum values before they can reach application logic | PASS |
| Least-privilege unaffected | collector container's `read_only`/`cap_drop: ALL`/`no-new-privileges` settings ([compose.yaml](../../compose.yaml)) are unchanged; the new table adds no new filesystem or network surface | PASS |
| Fail-closed on misconfiguration | `resolve_effective_targets` raises `ChannelPolicyError` (non-zero exit) if every configured target loses authorization, rather than silently running with nothing collected or falling back to an insecure default | PASS |
| No new external egress | `channel_policies` is read via the existing internal-only PostgreSQL connection; no new network destination introduced | PASS |

## Manual Review

Reviewed `channel_policy.py`, `retention_cleanup.py`, both new migrations, and both onboarding markdown files for any credential-shaped string (API hash patterns, session file paths, phone numbers, tokens). None found — every value is business/authorization metadata already discussed openly with the user in chat (channel IDs, topic IDs, symbol lists, authorization status words).

**Named, accepted trust-boundary note:** `channel_policies` itself has no append-only protection — anyone who can already `UPDATE` that table (the same role that can already change `gate_decision` to enable/disable collection at all) could shrink `raw_retention_days` to trigger earlier deletion of otherwise-recent data. This is not a new exposure introduced by retention cleanup; it's consistent with the pre-existing trust boundary that whoever can edit `channel_policies` already controls collection policy broadly.

## Not Covered By This Check

This document covers only what changed in Phase 3 (the Channel Policy table and its enforcement code). It does not re-evaluate the Phase 2 collector's own session/credential handling — see [../phase-2/security-check.md](../phase-2/security-check.md) for that, which remains valid and unaffected by this phase's changes.
