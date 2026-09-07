# Phase 2 Security Check

- **Revision:** `f4ccff6a039f42df35fc81054c9208861b42ecd3`
- **Branch:** `phase/2-telegram-readonly-collector`
- **Date:** 2026-09-06
- **Verdict:** PASS (scope: credential/session handling only — does not itself close Gate 2)
- **External Secrets Introduced:** `TELEGRAM_API_ID`、`TELEGRAM_API_HASH`、Telethon session (user-entered locally, never pasted into chat)

## Checks

| Control | Evidence | Result |
|---|---|---|
| Repository secret scan | `uv run python scripts/secret_scan.py --root .` → 73 files scanned, 0 findings | PASS |
| Session file location | `secrets/telegram/collector.session` exists on local disk only | PASS |
| Session file Git-ignored | `git check-ignore -v secrets/telegram/collector.session` → matched `.gitignore:24:secrets/`; `git status --short --ignored` lists `secrets/` under `!!` (ignored), not tracked | PASS |
| Session/credential absent from Git history | `git ls-files \| grep -i session` and `grep -i secret` return only scanner source files (`secret_scan.py`, tests), no session artifacts | PASS |
| Collector container log scan | `docker logs <collector>` (52 lines) grepped for `session\|api_hash\|api_id` → 0 matches | PASS |
| Chat transcript | No phone number, login code, 2FA code, API hash or session string was pasted into this conversation at any point | PASS |
| Credential handoff boundary | Phone number/login code/2FA entered only at the local PowerShell prompt (`telegram-bootstrap.ps1`), never echoed to stdout/log | PASS |
| Runtime least privilege | `collector` service: non-root, `read_only: true`, `tmpfs: /tmp`, `cap_drop: ALL`, `no-new-privileges` (`compose.yaml:107-113`) | PASS |
| Database exposure | `db` service has no host port mapping; only reachable on the internal Docker network (`compose.yaml`) | PASS |
| Collector network scope | `collector` only reaches `internal` (DB) and `edge` (Telegram MTProto); no unrelated egress configured | PASS |
| Read-only enforcement | Collector code path only calls Telethon read APIs (`get_entity`, `iter_messages`, `NewMessage`/`MessageEdited` listeners); no send/delete/edit calls present in `telegram_collector.py` or `telegram_readonly.py` | PASS |

## Manual Sensitive-Log Review

Reviewed the collector's structured JSON logs (`docker logs`) and the terminal output pasted into this conversation for the 2026-09-06 live validation run. Only non-sensitive fields appear: `channel_id`, `backfill_seen`, `checkpoint`, `timestamp`, `level`, `logger`, `message`, plus Telethon's own informational download/reconnect lines. No API hash, session string, phone number, login code, or 2FA code appears in any log line reviewed.

## Not Covered by This Check

This document only closes the **Gate 2 "Telegram session 不出現在 log、Git 或測試報告"** requirement. It does not evaluate: 24-hour soak stability, controlled-restart message integrity over a longer window, or manual message-count reconciliation against `@followgerry` — see [test-evidence.md](test-evidence.md) and [gate-2-checklist.md](gate-2-checklist.md) for current status of those items.
